import inspect
import typing
from collections.abc import Callable
from functools import partial, wraps
from typing import Any, Concatenate, Self, cast

from pydantic import BaseModel

from nicetv.extract_from_stack_gpt5 import (
    find_generic_alias_in_stack,
)
from nicetv.inject_locals import inject_locals
from nicetv.extract_from_stack_opus41 import extract_generic_context
from nicetv.extract_from_stack_sonnet45 import find_generic_in_stack

pydantic_model_metaclass = type(BaseModel)
_generic_alias = typing._GenericAlias


from types import new_class
from typing import get_origin, get_args
from weakref import WeakValueDictionary

# Cache so we don't explode the number of runtime classes
_SPECIALIZED_CACHE: WeakValueDictionary[tuple[type, tuple], type] = (
    WeakValueDictionary()
)


def _specialized_subclass(alias_or_proxy) -> type:
    origin = getattr(alias_or_proxy, "__origin__", None) or get_origin(alias_or_proxy)
    if not isinstance(origin, type):
        raise TypeError(f"Not a valid generic alias: {alias_or_proxy!r}")
    args = tuple(
        getattr(alias_or_proxy, "__args__", None) or get_args(alias_or_proxy) or ()
    )
    key = (origin, args)

    if (spec := _SPECIALIZED_CACHE.get(key)) is not None:
        return spec

    # Nice readable name like C[int, str]
    arg_str = ", ".join(getattr(a, "__name__", repr(a)) for a in args)
    name = f"{origin.__name__}[{arg_str}]" if args else origin.__name__

    def body(ns):
        # Preserve a breadcrumb to the alias so your machinery can read it
        ns["__orig_class__"] = getattr(alias_or_proxy, "__orig_class__", alias_or_proxy)
        ns["__qualname__"] = name
        ns["__origin__"] = origin
        ns["__args__"] = args
        ns["_inst"] = alias_or_proxy._inst
        ns["_name"] = alias_or_proxy._name
        ns["__init__"] = _GAProxy.__init__
        # _GAProxy.__dict__

    spec = new_class(
        name,
        (
            # _GAProxy,
            origin,
        ),
        {},
        body,
    )
    _SPECIALIZED_CACHE[key] = spec

    for f in _ga_class_fields:
        setattr(spec, f, getattr(_GAProxy, f))
    return spec


def _is_pydantic(cls):
    return (
        isinstance(cls, BaseModel)
        or isinstance(cls, pydantic_model_metaclass)
        or (
            isinstance(cls, type)
            and (
                issubclass(cls, BaseModel) or issubclass(cls, pydantic_model_metaclass)
            )
        )
    )


def _is_specialized_generic(cls):
    if _is_pydantic(cls):
        return hasattr(cls, "__pydantic_generic_metadata__")
    if isinstance(cls, typing._GenericAlias) or isinstance(cls, typing.GenericAlias):
        return True
    return (
        hasattr(cls, "__origin__")
        and hasattr(cls, "__args__")
        and hasattr(cls, "_inst")
        and hasattr(cls, "_name")
    )


def _make_patched_cgi(owner, parent):
    cgi = inspect.getattr_static(owner, "__class_getitem__", None)
    if isinstance(cgi, classmethod):
        # if cgi.__func__ is None:
        #     print("cgi.__func__ is None", cgi, owner)
        cgi = cgi.__func__
    if cgi is None:
        bound_cgi = getattr(owner, "__class_getitem__", None)
        if bound_cgi is None:
            return None
        cgi = getattr(bound_cgi, "__func__", None)
        if cgi is None:
            cgi = bound_cgi

    _base_cgi = cgi

    def _patched_cgi(cls, key, _base=_base_cgi):
        assert _base is _base_cgi
        alias = _base_cgi(cls, key)  # a types.GenericAlias
        assert not _is_pydantic(cls)
        # TODO Handle calls from instance, access __orig_class__
        return make_alias_instance_from_alias(_GAProxy, alias)  # our thin wrapper

    return _patched_cgi


def _make_patched_init_subclass(owner):
    _orig_init_subclass = inspect.getattr_static(owner, "__init_subclass__")
    if hasattr(_orig_init_subclass, "__func__"):
        if _orig_init_subclass.__func__.__name__ == "_patched_init_subclass":
            return None

    def _patched_init_subclass(cls, *a, **kw):
        super(owner, cls).__init_subclass__(*a, **kw)
        _install_ga_proxy(cls)
        return

    return _patched_init_subclass


def _install_ga_proxy(owner):
    if _is_pydantic(owner):
        return
    if (parent := getattr(owner, "_ga_proxy_installed__", None)) != owner:
        patched_cgi = _make_patched_cgi(owner, parent)
        if patched_cgi is not None:
            owner.__class_getitem__ = classmethod(patched_cgi)
        patched_init_subclass = _make_patched_init_subclass(owner)
        if patched_init_subclass is not None:
            # setattr(patched_init_subclass, "_is_patched_init_subclass", True)
            # setattr(
            #     patched_init_subclass,
            #     "_original_init_subclass",
            #     owner.__init_subclass__,
            # )
            owner.__init_subclass__ = classmethod(patched_init_subclass)
        owner._ga_proxy_installed__ = owner


class aliasclassmethod(classmethod):
    def __init__(self, func):
        super().__init__(func)
        self.func = func
        self.__name__ = getattr(func, "__name__", "aliasclassmethod")
        self.__doc__ = getattr(func, "__doc__")

    def __set_name__(self, owner, name):
        self.name = name
        _install_ga_proxy(owner)

    def __get__(self, instance, owner=None):
        if instance is not None:
            if hasattr(instance, "__orig_class__"):
                owner = instance.__orig_class__
            if owner is None:
                owner = instance.__class__
        return super().__get__(instance, owner)


class _TakesAlias[T, **P, R]:
    def __init__(self, func: "Callable[P, R] | classmethod[T, P, R]"):
        if not isinstance(func, classmethod):
            raise ValueError(
                f"TakesAlias must wrap a classmethod, got {type(func)} for {func}"
            )
            # # not doing this because we can't install proxy if
            # # we're an inner decorator
            # self.cm = None
            # self.__func__ = func
        self.cm = func
        self.__func__ = func.__func__
        self.__name__ = getattr(func, "__name__", "takes_alias")
        self.__doc__ = func.__doc__
        self.__wrapped__ = func
        func.__func__._ta_ref = self

    # def __call__(self, *args, **kwargs):
    #     return self.__func__(*args, **kwargs)

    def __set_name__(self, owner, name):
        # self.cm.__set_name__(owner, name)
        self.name = name
        _install_ga_proxy(owner)

    def __get__(self, instance, owner=None) -> Callable[P, R]:
        if instance is not None:
            if hasattr(instance, "__orig_class__"):
                owner = instance.__orig_class__
            if owner is None:
                owner = instance.__class__
        assert self.cm is not None
        return self.cm.__get__(instance, owner)

    # @classmethod
    # def specialize_super(cls, homecls, targetcls):


class _TakesAlias3[T, **P, R]:
    def __init__(self, func: "Callable[P, R] | classmethod[T, P, R]"):
        if not isinstance(func, classmethod):
            raise ValueError(
                f"TakesAlias must wrap a classmethod, got {type(func)} for {func}"
            )
            # # not doing this because we can't install proxy if
            # # we're an inner decorator
            # self.cm = None
            # self.__func__ = func
        self.cm = func
        self.__func__ = func.__func__
        self.__name__ = getattr(func, "__name__", "takes_alias")
        self.__doc__ = func.__doc__
        self.__wrapped__ = func

    # def __call__(self, *args, **kwargs):
    #     return self.__func__(*args, **kwargs)

    def __set_name__(self, owner, name):
        # self.cm.__set_name__(owner, name)
        self.name = name
        _install_ga_proxy(owner)

    def __get__(self, instance, owner=None) -> Callable[P, R]:
        orig = None
        if instance is not None:
            if hasattr(instance, "__orig_class__"):
                orig = instance.__orig_class__
            if orig is None:
                orig = instance.__class__
        assert self.cm is not None
        func = self.cm.__get__(instance, owner)

        return partial(func, owner)

    # @classmethod
    # def specialize_super(cls, homecls, targetcls):


class tasuper(super):
    def __init__(self, t: Any = None, obj: Any = None, /) -> None:
        # self._this_class = None
        self._self_cls = None
        self._self = None

        if isinstance(obj, _GAProxy):
            orig = obj.__origin__
        else:
            orig = obj
        super().__init__(t, orig)
        # self.__thisclass__ = obj
        self.__self__ = obj
        self.__self_class__ = obj

    # def __getattribute__(self, name: str) -> Any:
    #     if name in ["__self__", "__self_class__", "_self", "_self_cls"]:
    #         return super().__getattribute__(name)
    #     raw = inspect.getattr_static(self.__self_class__, name)
    #     if _is_aliasclassmethod(raw):
    #         return raw.__get__(None, self.__self__)

    #     return super().__getattribute__(name)

    # def __getattr__(self, name):
    #     return getattr(self.__self_class__, name)
    #     if _is_aliasclassmethod(raw):
    #         return raw.__get__(None, self.__self__)
    #     return super().__getattr__(name)

    @property
    def __self__(self):
        return self._self

    @__self__.setter
    def __self__(self, value):
        self._self = value

    @__self__.deleter
    def __self__(self):
        del self._self

    @property
    def __self_class__(self):
        return self._self_cls

    @__self_class__.setter
    def __self_class__(self, value):
        self._self_cls = value

    @__self_class__.deleter
    def __self_class__(self):
        del self._self_cls

    # @property
    # def __thisclass__(self):
    #     return self._this_class

    # @__thisclass__.setter
    # def __thisclass__(self, value):
    #     self._this_class = value

    # @__thisclass__.deleter
    # def __thisclass__(self):
    #     del self._this_class


def _super(owner: type | None = None, obj: object | None = None, *, level: int = 0):
    for i in range(level, level + 21):
        try:
            return _super_base(owner, obj, level=i)
        except Exception as e:
            pass
    return _super_base(owner, obj, level=i + 1)


def _super_base(
    owner: type | None = None,
    obj: object | None = None,
    *,
    level: int = 1,
):
    """
    Return a `super(...)` bound like zero-arg `super()` from the caller's context.

    - By default (`owner is None and obj is None`), it inspects the caller's frame
      at `level` (1 = direct caller) and expects two things, just like real zero-arg super():
        • a `__class__` cell in locals,
        • a first positional local (usually `self` or `cls`).
    - If `owner` and `obj` are provided, it simply returns `super(owner, obj)`.

    Notes:
      • Works inside instance methods and classmethods that were defined in a class body.
      • Won’t work in a `staticmethod` (no first arg) or in wrappers defined outside the class
        unless you pass `owner`/`obj` (or bump `level` to look past the wrapper that still
        preserves the `__class__` cell in the next frame).
    """
    from builtins import super

    if owner is not None or obj is not None:
        if owner is None or obj is None:
            raise TypeError("Provide both owner= and obj=, or neither.")
        return super(owner, obj)

    frame = inspect.currentframe()
    if frame is None:
        raise RuntimeError("No Python frame available.")

    try:
        # Walk up to the requested caller
        for _ in range(level):
            frame = frame.f_back
            if frame is None:
                raise RuntimeError("Not enough stack frames.")

        locals_ = frame.f_locals
        code = frame.f_code
        varnames = code.co_varnames

        if not varnames:
            raise TypeError("Caller has no positional locals (not a method?).")

        first_name = varnames[0]
        if first_name not in locals_:
            # Happens e.g. before the function has bound its first arg
            raise TypeError("Caller’s first parameter is not bound yet.")

        first_arg = locals_[first_name]

        try:
            owner_cls = locals_["__class__"]
        except KeyError as exc:
            # This means the function wasn't defined in a class body (no __class__ cell).
            # Using type(first_arg) here would be subtly wrong for inherited methods,
            # so we error out instead of guessing.
            raise TypeError(
                "No __class__ cell found in caller; define the method in a class body "
                "or pass owner=/obj= explicitly."
            ) from exc

        if isinstance(first_arg, _GAProxy):
            return wsuper(owner_cls, first_arg)

        return super(owner_cls, first_arg)

    finally:
        # Help GC break potential reference cycles
        del frame


class wsuper:
    def __init__(self, t: Any = None, obj: Any = None):
        if isinstance(obj, _GAProxy):
            orig = obj.__origin__
        else:
            orig = obj
        self._sup = super(t, orig)
        self.obj = obj

    def __getattr__(self, name):
        got = getattr(self._sup, name)
        if hasattr(got, "__func__"):
            if hasattr(got.__func__, "_ta_ref"):
                ta = got.__func__._ta_ref
                return ta.__get__(None, self.obj)
        # if _is_aliasclassmethod(got):
        #     return got.__get__(None, self.obj)
        # elif hasattr(got, "__func__"):
        #     if isinstance(got.__func__, _TakesAlias):
        #         return got.__func__.__get__(None, self.obj)
        return got


class _TakesAlias2[T, **P, R]:
    def __init__(self, func: "Callable[P, R] | classmethod[T, P, R]"):
        if not isinstance(func, classmethod):
            raise ValueError(
                f"TakesAlias must wrap a classmethod, got {type(func)} for {func}"
            )
            # # not doing this because we can't install proxy if
            # # we're an inner decorator
            # self.cm = None
            # self.__func__ = func
        self.cm = func
        self.__func__ = func.__func__
        self.__name__ = getattr(func, "__name__", "takes_alias")
        self.__doc__ = func.__doc__
        self.__wrapped__ = func

    # def __call__(self, *args, **kwargs):
    #     return self.__func__(*args, **kwargs)

    def __set_name__(self, owner, name):
        # self.cm.__set_name__(owner, name)
        self.name = name
        # _install_ga_proxy(owner)

    def __get__(self, instance, owner=None) -> Callable[P, R]:
        if instance is not None:
            if hasattr(instance, "__orig_class__"):
                owner = instance.__orig_class__
            if owner is None:
                owner = instance.__class__
        if owner is not None and not _is_specialized_generic(owner):
            new_owner = find_generic_alias_in_stack(owner)
            if new_owner:
                owner = new_owner
            else:
                find_generic_in_stack(owner)
                extract_generic_context()
                print()
                2
                current_typing_generic_for(cls, max_depth=200)

        assert self.cm is not None
        return self.cm.__get__(instance, owner)


#     func: Callable[P, R],
# ) -> Callable[P, R]:
def takes_alias[**P, R](
    func: Callable[P, R],
) -> Callable[P, R]:
    assert isinstance(func, classmethod)
    newfunc = inject_locals(super=_super, _decorator_name="takes_alias")(func.__func__)
    # assert isinstance(newfunc, classmethod)
    func = classmethod(newfunc)
    return wraps(func)(cast(Callable[P, R], _TakesAlias(func)))


def takes_alias3[T, **P, R](
    func: Callable[Concatenate[T, P], R],
) -> Callable[P, R]:
    return wraps(func)(cast(Callable[P, R], _TakesAlias3(func)))


# def takes_alias(func):
#     if not hasattr(func, "_acm_takes_alias"):
#         func._acm_takes_alias = True
#     return func


def _is_aliasclassmethod(obj):
    return (
        isinstance(obj, _TakesAlias)
        or isinstance(obj, aliasclassmethod)
        or getattr(obj, "_acm_takes_alias", False)
        or (
            isinstance(obj, classmethod)
            and (
                isinstance(obj.__func__, _TakesAlias)
                or getattr(obj.__func__, "_acm_takes_alias", False)
            )
        )
    )


_ga_fields = [
    "_inst",
    "_name",
    "__origin__",
    "__call__",
    "__mro_entries__",
    "__getattr__",
    "__dir__",
    "__getitem__",
    "_determine_new_args",
    "_make_substitution",
    "copy_with",
    "__repr__",
    "__reduce__",
    "__mro_entries__",
    "__iter__",
    "__args__",
    "__slots__",
    "__parameters__",
]
_ga_instance_fields = [
    "_inst",
    "_name",
    "__origin__",
    "__args__",
    "__parameters__",
]
_ga_class_fields = [
    "__call__",
    "__mro_entries__",
    "__getattr__",
    "__dir__",
    "__getitem__",
    "_determine_new_args",
    "_make_substitution",
    "copy_with",
    "__repr__",
    "__reduce__",
    "__mro_entries__",
    "__iter__",
    # "__slots__",
]
from typing import Generic, Protocol
# import typing._collect_type_parameters, typing._TypingEllipsis


class _GAProxy(
    typing._GenericAlias,
    _root=True,
):
    # def __call__(self, *args, **kwargs):  # TODO maybe generic init would be nice?
    #     return self.__origin__(*args, **kwargs)

    # def __init__(self, origin, args, inst, name):
    #     self._inst = inst
    #     self._name = name
    #     self.__origin__ = origin
    #     self.__slots__ = None  # This is not documented.

    #     if not isinstance(args, tuple):
    #         args = (args,)
    #     self.__args__ = tuple(... if a is typing._TypingEllipsis else a for a in args)
    #     enforce_default_ordering = origin in (Generic, Protocol)
    #     self.__parameters__ = typing._collect_type_parameters(
    #         args,
    #         enforce_default_ordering=enforce_default_ordering,
    #     )
    #     if not name:
    #         self.__module__ = origin.__module__

    # def specialize_super(self, cls):

    #  I think currently it gets the _GAProxy as __orig_class__

    # there are benefits to each but I'm currently leaning towards
    # having the _GAProxy as __orig_class__
    # will have to notice if this causes issues down the line.
    #     # Allow C[int](...) to construct instances like C(...)
    #     return self._gaproxy_alias(*args, **kwargs)
    # def __new__(cls, origin, args, inst, name):
    #     # inst = super().__new__(cls, )
    #     if cls.__name__.startswith("_proxy"):
    #         return super().__new__(cls)
    #     else:
    #         t = type(
    #             f"_proxy{cls.__name__}{origin.__name__}[{','.join([str(a) for a in args])}]",
    #             (
    #                 cls,
    #                 origin,
    #             ),
    #             {},
    #         )
    #     return t(origin, args, inst=inst, name=name)

    # def __init_subclass__(cls, *args, **kwargs):
    #     pass

    def __getattribute__(self, name):
        if name in _ga_fields:
            return typing._GenericAlias.__getattribute__(self, name)
        origin = self.__origin__

        raw = inspect.getattr_static(origin, name)
        if _is_aliasclassmethod(raw):
            return raw.__get__(None, self)

        return getattr(self.__origin__, name)

    def __getattr__(self, name):
        origin = self.__origin__

        raw = inspect.getattr_static(origin, name)
        if _is_aliasclassmethod(raw):
            # alias = typing._GenericAlias(
            #     origin=self.__origin__,
            #     args=self.__args__,
            #     inst=self._inst,
            #     name=self._name,
            # )

            # better to return self than alias because if we return alias,
            # acm that calls other acm fails on the second call
            return raw.__get__(None, self)
        return getattr(origin, name)

    # @property
    # def __mro__(self):
    #     return self.__origin__.__mro__

    # @property
    # def __bases__(self):
    #     return self.__origin__.__bases__

    # @property
    # def __class__(self):
    #     return self.__origin__.__class__

    # @property
    # def __dict__(self):
    #     return self.__origin__.__dict__

    # @property
    # def __name__(self):
    #     return self.__origin__.__name__

    # @property
    # def __annotations__(self):
    #     return self.__origin__.__annotations__

    # @property
    # def __wrapped__(self):
    #     return self.__origin__

    # @property
    # def __staticmethods__(self):
    #     return self.__origin__.__staticmethods__

    # @property
    # def __type__(self):
    #     return self.__origin__

    # @property
    # def __base__(self):
    #     return self.__origin__.__base__


# def make_alias_instance_from_alias(alias_cls, alias):
#     return alias_cls(
#         origin=alias.__origin__,
#         args=alias.__args__,
#         inst=alias._inst,
#         name=alias._name,
#     )


def make_alias_instance_from_alias(alias_cls, alias):
    # class CLS(alias_cls, metaclass=alias.__origin__, _root=True): ...

    obj = alias_cls(
        origin=alias.__origin__,
        args=alias.__args__,
        inst=alias._inst,
        name=alias._name,
    )
    return obj
