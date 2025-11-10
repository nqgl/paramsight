import inspect
import typing

from paramsight._is_aliasclassmethod import _is_aliasclassmethod

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


class _GAProxy(  # type:ignore
    typing._GenericAlias,  # type:ignore[name-defined]
    _root=True,  # type:ignore[arg-type]
):
    def __getattribute__(self, name):
        if name in _ga_fields:
            return typing._GenericAlias.__getattribute__(self, name)  # type:ignore[name-defined]
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
