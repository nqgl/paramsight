# Python 3.13  (PEP 695 generics)
import inspect
import types


class aliasclassmethod:
    """
    A classmethod that, when reached through C[...], receives the GenericAlias
    (e.g., C[int]) as 'cls'. Access via C.check() still receives the origin class.
    """

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
                def _base_cgi(cls, key):
                    return super().__class_getitem__(key)
            else:
                # The class already defines __class_getitem__; wrap it.
                def _base_cgi(cls, key, _raw=raw):
                    # _raw
                    return _raw(key)

            def _patched_cgi(cls, key, _base=_base_cgi):
                alias = _base(cls, key)  # a types.GenericAlias
                return _GAProxy(alias)  # our thin wrapper

            owner.__class_getitem__ = classmethod(_patched_cgi)
            owner._ga_proxy_installed__ = True

    def __get__(self, obj, owner=None):
        # Behave like a normal classmethod when accessed on the class.
        # obj.__orig_class__
        return types.MethodType(self.func, owner)


class _GAProxy:
    """
    Wraps a GenericAlias and rebinds ONLY aliasclassmethod descriptors so that
    'cls' is the alias (e.g., C[int]). Everything else is forwarded.
    """

    __slots__ = ("_alias",)

    def __init__(self, alias):
        self._alias = alias

    def __repr__(self):
        return repr(self._alias)

    def __call__(self, *args, **kwargs):
        # Allow C[int](...) to construct instances like C(...)
        return self._alias(*args, **kwargs)

    def __getattr__(self, name):
        origin = self._alias.__origin__
        # Get the raw descriptor without binding
        raw = inspect.getattr_static(origin, name)
        if isinstance(raw, aliasclassmethod):
            # Bind the decorated classmethod with the *alias* as the owner,
            # so inside the method 'cls' is C[int] (or whatever alias you used).
            return raw.__get__(None, self._alias)
        # Everything else (regular classmethods, staticmethods, attrs, etc.)
        # falls back to the origin's normal binding behavior.
        return getattr(origin, name)

    # (Optional niceties—uncomment if you need parity with GenericAlias)
    def __eq__(self, other):
        return self._alias == getattr(other, "_alias", other)

    def __hash__(self):
        return hash(self._alias)

    @property
    def __origin__(self):
        return self._alias.__origin__

    @property
    def __args__(self):
        return self._alias.__args__


# ------------------ DEMO ------------------


class C[T: float = int]:
    def __init__(self):
        self.value = 1

    @aliasclassmethod
    def check_class(cls):
        print(f"cls {cls}")

    @classmethod
    def normal(cls):
        print(f"normal sees {cls}")


c = C[int]()
print(c.__orig_class__)
C.check_class()  # -> cls <class '__main__.C'>
C[int].check_class()  # -> cls C[int]
C[int].normal()  # -> normal sees <class '__main__.C'>
type(c)
c.check_class()
c.normal()
