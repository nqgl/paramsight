# import typing._collect_type_parameters, typing._TypingEllipsis


import inspect
import typing

from nicetv._is_aliasclassmethod import _is_aliasclassmethod

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
