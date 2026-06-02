import inspect
import typing
from typing import Any

from paramsight._is_aliasclassmethod import _is_aliasclassmethod
from paramsight.slotted_strategies import (
    _has_orig_class_storage,
    _slot_strategy,
    get_synth,
    remember_orig_class,
)

_generic_alias_fields = [
    # ``__class__`` must report the proxy's *own* type, not be delegated to the
    # origin. Delegating returns the origin's metaclass (``type``), which makes
    # ``isinstance(proxy, type)`` true -- so ``typing._type_repr`` (used by union
    # repr) treats the proxy as a class and prints it without its args
    # (``Box`` instead of ``Box[int]``). A normal generic alias reports its own
    # type here; the proxy must too.
    "__class__",
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
    "__reduce_ex__",
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
        if name in _generic_alias_fields:
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

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = super().__call__(*args, **kwargs)
        # ``typing._GenericAlias.__call__`` already tried
        # ``result.__orig_class__ = self`` and swallowed any failure. Cases
        # when it didn't stick:
        #   1. The class has storage for ``__orig_class__`` (real slot or
        #      attrs/dataclass field) but ``__setattr__`` rejected the write
        #      (typical for ``@frozen``). Bypass via ``object.__setattr__``;
        #      if even that fails, re-raise -- storage was detected, so the
        #      caller would otherwise silently lose the parametrization.
        #   2. ``_paramsight_slots = "class_swap"``: swap ``__class__`` to a
        #      cached synthetic ``__slots__=()`` subclass carrying the alias
        #      (layout-compatible, so it works on slotted/frozen instances).
        #   3. ``_paramsight_slots = "side_table"``: record out of band. Raise
        #      loudly here if the instance can't be tracked at all (not
        #      weakref-able) -- silent untracked-ness is the trap we prevent.
        #   4. No storage, no opt-in: do nothing here; ``_TakesAlias.__get__``
        #      / ``TypeVarValue.__get__`` will raise *if* the instance is ever
        #      looked up there. (Class-side use never needs ``__orig_class__``.)
        if getattr(result, "__orig_class__", None) is not self:
            cls = type(result)
            if _has_orig_class_storage(cls):
                object.__setattr__(result, "__orig_class__", self)
            elif _slot_strategy(cls) == "class_swap":
                object.__setattr__(result, "__class__", get_synth(self))
            elif _slot_strategy(cls) == "side_table":
                if not remember_orig_class(result, self):
                    raise TypeError(
                        f"paramsight: cannot record the parametrization of "
                        f"{self!r}: instances of {cls.__name__} are slotted "
                        f"and not weakref-able (no ``__weakref__`` slot). "
                        f"Add ``weakref_slot=True`` to your @dataclass / "
                        f"@define, or declare ``__orig_class__: type | None "
                        f"= None`` on the class to use a real field instead."
                    )
        return result
