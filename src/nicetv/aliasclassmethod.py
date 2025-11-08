import inspect
import typing
from collections.abc import Callable
from functools import wraps
from typing import cast

from pydantic import BaseModel

pydantic_model_metaclass = type(BaseModel)


def _is_pydantic(cls):
    return isinstance(cls, pydantic_model_metaclass) or issubclass(cls, BaseModel)


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


#     func: Callable[P, R],
# ) -> Callable[P, R]:
def takes_alias[**P, R](
    func: Callable[P, R],
) -> Callable[P, R]:
    return wraps(func)(cast(Callable[P, R], _TakesAlias(func)))


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


class _GAProxy(
    typing._GenericAlias,
    _root=True,
):
    # def __call__(self, *args, **kwargs): # TODO maybe generic init would be nice?
    #  I think currently it gets the _GAProxy as __orig_class__
    # there are benefits to each but I'm currently leaning towards
    # having the _GAProxy as __orig_class__
    # will have to notice if this causes issues down the line.
    #     # Allow C[int](...) to construct instances like C(...)
    #     return self._gaproxy_alias(*args, **kwargs)

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


def make_alias_instance_from_alias(alias_cls, alias):
    return alias_cls(
        origin=alias.__origin__,
        args=alias.__args__,
        inst=alias._inst,
        name=alias._name,
    )
