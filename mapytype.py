import inspect
import types
import typing
from typing import Callable, Concatenate, overload


class aliasclassmethod:
    def __init__(self, func):
        self.func = func
        self.__name__ = getattr(func, "__name__", "aliasclassmethod")
        self.__doc__ = getattr(func, "__doc__")

    def __set_name__(self, owner, name):
        self.name = name
        # One-time patch: make C[...] return a tiny proxy that only rewires
        # aliasclassmethod descriptors. Everything else behaves normally.
        if not getattr(owner, "_ga_proxy_installed__", False):
            raw = getattr(owner, "__class_getitem__", None)  # raw, not bound

            if raw is None:
                # Default path: delegate to type.__class_getitem__ (built-in)
                # TODO called wrong
                # TODO I would think we should just return here and not install,
                # except what happens if you subclass this again?
                def _base_cgi(cls, key):
                    return super().__class_getitem__(key)
            else:
                # The class already defines __class_getitem__; wrap it.
                def _base_cgi(cls, key, _raw=raw):
                    # _raw
                    return _raw(key)

            def _patched_cgi(cls, key, _base=_base_cgi):
                alias = _base(cls, key)  # a types.GenericAlias
                # TODO Handle calls from instance, access __orig_class__
                return _GAProxy(alias)  # our thin wrapper

            owner.__class_getitem__ = classmethod(_patched_cgi)
            owner._ga_proxy_installed__ = True

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
    # typing._GenericAlias, # TODO maybe?
    # root=True,
):
    __slots__ = ("_gaproxy_alias",)

    def __init__(self, alias):
        self._gaproxy_alias = alias

    def __repr__(self):
        return repr(self._gaproxy_alias)

    def __call__(self, *args, **kwargs):
        # Allow C[int](...) to construct instances like C(...)
        return self._gaproxy_alias(*args, **kwargs)

    def __getattr__(self, name):
        origin = self._gaproxy_alias.__origin__
        # Get the raw descriptor without binding
        raw = inspect.getattr_static(origin, name)
        if isinstance(raw, aliasclassmethod):
            # Bind the decorated classmethod with the *alias* as the owner,
            # so inside the method 'cls' is C[int] (or whatever alias you used).
            return raw.__get__(None, self._gaproxy_alias)
        # Everything else (regular classmethods, staticmethods, attrs, etc.)
        # falls back to the origin's normal binding behavior.
        return getattr(origin, name)

    def __eq__(self, other):
        return self._gaproxy_alias == getattr(other, "_gaproxy_alias", other)

    def __hash__(self):
        return hash(self._gaproxy_alias)

    @property
    def __origin__(self):
        return self._gaproxy_alias.__origin__

    @property
    def __args__(self):
        return self._gaproxy_alias.__args__
