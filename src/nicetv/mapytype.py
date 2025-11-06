import inspect
import types
from typing import Generic
from pydantic._internal._model_construction import ModelMetaclass
from pydantic import BaseModel
import typing

pydantic_model_metaclass = type(BaseModel)
# __pydantic_generic_metadata__


def _is_pydantic(cls):
    return isinstance(cls, pydantic_model_metaclass) or issubclass(cls, BaseModel)


def _make_patched_cgi(owner, parent):
    # raw = owner.__dict__.get("__class_getitem__", None)
    # parent.__class_getitem__
    # owner.__class_getitem__.__func__
    # owner[int]

    #     def _base_cgi_raw(cls, key):
    #         return raw(cls, key)

    #     _base_cgi = _base_cgi_raw
    # else:
    import inspect

    cgi = inspect.getattr_static(owner, "__class_getitem__", None)
    if isinstance(cgi, classmethod):
        if cgi.__func__ is None:
            print("cgi.__func__ is None", cgi, owner)
        cgi = cgi.__func__
    # else:
    #     bound_cgi = getattr(owner, "__class_getitem__", None)

    # cgi.__func__ is None
    # bound_cgi.__func__ is cgi.__func__
    if cgi is None:
        bound_cgi = getattr(owner, "__class_getitem__", None)
        if bound_cgi is None:
            return None
        cgi = getattr(bound_cgi, "__func__", None)
        if cgi is None:
            cgi = bound_cgi

    # if bound_cgi is not None:
    #     isinstance(bound_cgi, classmethod)
    #     isinstance(bound_cgi, types.MethodType)
    #     cgi = getattr(bound_cgi, "__func__", None)
    #     cgi
    #     object.__class_getitem__
    # cgi = bound_cgi.__func__

    def _base_cgi_bound(cls, key):
        owner
        # # inspect.getattr_static(owner, "__class_getitem__")
        # bound_cgi
        # cgi
        # raw
        if False:
            bound_cgi(int)

        # if cgi is None:
        #     return cls
        return cgi(cls, key)

    _base_cgi = _base_cgi_bound

    def _patched_cgi(cls, key, _base=_base_cgi):
        print(f" called patched_cgi {owner.__name__}->{cls.__name__}[{key}]")
        assert _base is _base_cgi
        alias = _base_cgi(cls, key)  # a types.GenericAlias
        assert not _is_pydantic(cls)
        # TODO Handle calls from instance, access __orig_class__
        return make_alias_instance_from_alias(_GAProxy, alias)  # our thin wrapper

    return _patched_cgi


def _make_patched_init_subclass(owner):
    # if isinstance(owner.__init_subclass__, types.BuiltinFunctionType) or isinstance(
    #     owner.__init_subclass__, types.BuiltinMethodType
    # ):
    #     _orig_init_subclass = inspect.getattr_static(owner, "__init_subclass__")

    #     def orig_init_subclass(cls, *a, **kw):
    #         # return
    #         super(owner, cls).__init_subclass__(*a, **kw)
    #         # _orig_init_subclass(cls)
    #         # _orig_init_subclass

    # else:
    #     # orig_init_subclass = owner.__init_subclass__.__func__
    #     orig_init_subclass = inspect.getattr_static(owner, "__init_subclass__")
    #     # owner._prev_orig_init_subclass = orig_init_subclass
    #     # inspect.getattr_static(owner, "__init_subclass__")
    #     prev_orig = [orig_init_subclass]
    #     if hasattr(orig_init_subclass, "_is_patched_init_subclass"):
    #         orig_init_subclass = orig_init_subclass._original_init_subclass
    #         prev_orig.append(orig_init_subclass)
    #     if hasattr(orig_init_subclass, "__func__"):
    #         orig_init_subclass = orig_init_subclass.__func__
    #         prev_orig.append(orig_init_subclass)
    #         print("prev_orig", prev_orig)

    _orig_init_subclass = inspect.getattr_static(owner, "__init_subclass__")
    if hasattr(_orig_init_subclass, "__func__"):
        if _orig_init_subclass.__func__.__name__ == "_patched_init_subclass":
            return None

    def orig_init_subclass(cls, *a, **kw):
        # return
        super(owner, cls).__init_subclass__(*a, **kw)
        # _orig_init_subclass(cls)
        # _orig_init_subclass

    def _patched_init_subclass(cls, *a, **kw):
        super(owner, cls).__init_subclass__(*a, **kw)

        owner
        print("args", a)
        print("kwargs", kw)
        print("called init_subclass", cls)
        # if isinstance(orig_init_subclass, types.BuiltinFunctionType) or isinstance(
        #     orig_init_subclass, types.BuiltinMethodType
        # ):
        #     orig_init_subclass()
        # else:
        # orig_init_subclass(cls)
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
        # owner.__init_subclass__ = classmethod(_make_patched_init_subclass(owner))
        # if False:
        # if parent is None:
        patched_init_subclass = _make_patched_init_subclass(owner)
        if patched_init_subclass is not None:
            setattr(patched_init_subclass, "_is_patched_init_subclass", True)
            setattr(
                patched_init_subclass,
                "_original_init_subclass",
                owner.__init_subclass__,
            )
            owner.__init_subclass__ = classmethod(patched_init_subclass)
        owner._ga_proxy_installed__ = owner
    else:
        print("owner already has _ga_proxy_installed__", owner)


class aliasclassmethod(classmethod):
    def __init__(self, func):
        self.func = func
        self.__name__ = getattr(func, "__name__", "aliasclassmethod")
        self.__doc__ = getattr(func, "__doc__")

    def __set_name__(self, owner, name):
        self.name = name
        # One-time patch: make C[...] return a tiny proxy that only rewires
        # aliasclassmethod descriptors. Everything else behaves normally.
        # if not getattr(owner, "_ga_proxy_installed__", False):
        #     owner.__init_subclass__
        #     raw = getattr(owner, "__class_getitem__", None)  # raw, not bound
        _install_ga_proxy(owner)
        #     if raw is None:
        #         # Default path: delegate to type.__class_getitem__ (built-in)
        #         # TODO called wrong
        #         # TODO I would think we should just return here and not install,
        #         # except what happens if you subclass this again?
        #         owner

        #         def _base_cgi(cls, key):
        #             cls.__class_getitem__
        #             return Generic.__class_getitem__(key)
        #     else:
        #         # The class already defines __class_getitem__; wrap it.
        #         def _base_cgi(cls, key, _raw=raw):
        #             # _raw
        #             return _raw(key)

        #     def _patched_cgi(cls, key, _base=_base_cgi):
        #         alias = _base(cls, key)  # a types.GenericAlias
        #         # TODO Handle calls from instance, access __orig_class__
        #         return _GAProxy(alias)  # our thin wrapper

        #     owner.__class_getitem__ = classmethod(_patched_cgi)
        #     owner._ga_proxy_installed__ = True

    def __get__(self, instance, owner=None):
        # Behave like a normal classmethod when accessed on the class.
        # obj.__orig_class__
        if instance is not None:
            if hasattr(instance, "__orig_class__"):
                owner = instance.__orig_class__
            if owner is None:
                owner = instance.__class__
        return types.MethodType(self.func, owner)


class _GAProxy(
    typing._GenericAlias,
    _root=True,
):
    # def __call__(self, *args, **kwargs): # TODO maybe generic init would be nice?
    #     # Allow C[int](...) to construct instances like C(...)
    #     return self._gaproxy_alias(*args, **kwargs)

    def __getattr__(self, name):
        # if name == "_gaproxy_alias":
        #     object.__getattribute__(self, __slots__)
        #     self.__slots__

        #
        origin = self.__origin__

        # Get the raw descriptor without binding
        raw = inspect.getattr_static(origin, name)
        if isinstance(raw, aliasclassmethod):
            # Bind the decorated classmethod with the *alias* as the owner,
            # so inside the method 'cls' is C[int] (or whatever alias you used).

            # alias = typing._GenericAlias(
            #     origin=self.__origin__,
            #     args=self.__args__,
            #     inst=self._inst,
            #     name=self._name,
            # )
            # better to return self than alias because if we return alias,
            # acm that calls other acm fails on the second call
            return raw.__get__(None, self)
        # Everything else (regular classmethods, staticmethods, attrs, etc.)
        # falls back to the origin's normal binding behavior.
        return getattr(origin, name)


def make_alias_instance_from_alias(alias_cls, alias):
    return alias_cls(
        origin=alias.__origin__,
        args=alias.__args__,
        inst=alias._inst,
        name=alias._name,
    )


# class _GAProxy(
#     typing._GenericAlias,
#     _root=True,
# ):
#     __slots__ = (
#         # "__weakref__",
#         "_gaproxy_alias",
#     )

#     def __init__(self, alias):
#         # self.__slots__ = ("_gaproxy_alias",)
#         self._gaproxy_alias = alias

#     def __repr__(self):
#         return repr(self._gaproxy_alias)

#     def __call__(self, *args, **kwargs):
#         # Allow C[int](...) to construct instances like C(...)
#         return self._gaproxy_alias(*args, **kwargs)

#     def __getattr__(self, name):
#         # if name == "_gaproxy_alias":
#         #     object.__getattribute__(self, __slots__)
#         #     self.__slots__

#         #     return super().__getattr__(name)
#         origin = self._gaproxy_alias.__origin__
#         # Get the raw descriptor without binding
#         raw = inspect.getattr_static(origin, name)
#         if isinstance(raw, aliasclassmethod):
#             # Bind the decorated classmethod with the *alias* as the owner,
#             # so inside the method 'cls' is C[int] (or whatever alias you used).
#             return raw.__get__(None, self._gaproxy_alias)
#         # Everything else (regular classmethods, staticmethods, attrs, etc.)
#         # falls back to the origin's normal binding behavior.
#         return getattr(origin, name)

#     def __eq__(self, other):
#         return self._gaproxy_alias == getattr(other, "_gaproxy_alias", other)

#     def __hash__(self):
#         return hash(self._gaproxy_alias)

#     @property
#     def __origin__(self):
#         return self._gaproxy_alias.__origin__

#     @property
#     def __args__(self):
#         return self._gaproxy_alias.__args__
