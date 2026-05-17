"""Slotted-class handling for ``__orig_class__``.

When you call a generic alias (``Box[int](...)``), CPython tries to stamp
``result.__orig_class__ = Box[int]`` on the new instance. That silently fails
for instances of slotted classes built by ``attrs.define`` / ``@dataclass``
with ``slots=True``: they have no ``__dict__`` and no ``__orig_class__`` slot.

paramsight refuses to silently lose parametrization. The check fires at the
point it would actually matter -- an instance-side ``@takes_alias`` /
``TypeVarValue`` lookup that can't recover the alias. (Class-side use --
``Cls[T].method(...)`` -- never needs to recover ``__orig_class__`` from an
instance, so a slotted class used purely as a factory is fine with no opt-in.)

When instance-side use is intended, opt into one of the strategies (class-body
marker ``_paramsight_slots = "..."``, or the equivalent decorator):

- ``@add_field`` -- declare a real ``__orig_class__`` field. Survives
  ``attrs.evolve`` / ``copy`` / ``pickle``. See :func:`add_field`.
- ``@uses_class_swap`` -- :mod:`.class_swap`. No field pollution; survives
  evolve/copy/pickle; ``type(inst) is Cls`` becomes ``False``.
- ``@uses_side_table`` -- :mod:`.side_table`. No field pollution; does NOT
  survive evolve/copy/pickle; needs weakref-able instances.
"""

from typing import Any

from paramsight.slotted_strategies import side_table
from paramsight.slotted_strategies.class_swap import (
    get_synth,
    is_creating_synth,
    uses_class_swap,
)
from paramsight.slotted_strategies.side_table import (
    remember_orig_class,
    uses_side_table,
)

__all__ = [
    "add_field",
    "uses_side_table",
    "uses_class_swap",
    "get_orig_class",
    "remember_orig_class",
    "get_synth",
    "is_creating_synth",
]


def _is_effectively_slotted(cls: type) -> bool:
    """True if instances of ``cls`` have no ``__dict__`` -- i.e. arbitrary
    attribute assignment fails.

    Uses CPython's ``__dictoffset__``: 0 means "no per-instance ``__dict__``."
    (Walking the MRO looking for missing ``__slots__`` is *not* equivalent --
    e.g. ``typing.Generic`` declares no ``__slots__`` yet contributes no
    ``__dict__``, because its ``__dictoffset__`` is 0.)
    """
    return getattr(cls, "__dictoffset__", -1) == 0


def _orig_class_declared(cls: type) -> bool:
    """True if ``__orig_class__`` is explicitly declared on ``cls`` (or its
    MRO) as a ``__slots__`` entry or an attrs/dataclass field -- independent
    of whether the class is slotted."""
    for c in cls.__mro__:
        slots = c.__dict__.get("__slots__") or ()
        if isinstance(slots, str):
            slots = (slots,)
        if "__orig_class__" in slots:
            return True
    attrs_attrs = getattr(cls, "__attrs_attrs__", None)
    if attrs_attrs and any(a.name == "__orig_class__" for a in attrs_attrs):
        return True
    dc_fields = getattr(cls, "__dataclass_fields__", None)
    if dc_fields and "__orig_class__" in dc_fields:
        return True
    return False


def _has_orig_class_storage(cls: type) -> bool:
    """True if instances of ``cls`` can carry ``__orig_class__``: either via
    ``__dict__``, a ``__slots__`` entry, or an attrs/dataclass field."""
    return not _is_effectively_slotted(cls) or _orig_class_declared(cls)


_VALID_SLOT_STRATEGIES = frozenset({"side_table", "class_swap"})


def _slot_strategy(owner: type) -> str | None:
    """The ``_paramsight_slots`` strategy declared on ``owner`` (or its MRO).

    Returns one of ``_VALID_SLOT_STRATEGIES`` or ``None``. Raises ``ValueError``
    for an unrecognised value so typos surface at first lookup.
    """
    strategy = getattr(owner, "_paramsight_slots", None)
    if strategy is None:
        return None
    if strategy not in _VALID_SLOT_STRATEGIES:
        raise ValueError(
            f"{owner.__name__}._paramsight_slots = {strategy!r}: must be one "
            f"of {sorted(_VALID_SLOT_STRATEGIES)} or unset."
        )
    return strategy


def add_field(cls: type) -> type:
    """Class decorator: declare ``__orig_class__: type | None = None`` on the
    class (if not already declared), giving a slotted class real-field storage
    for its parametrization.

    Place it *below* ``@attrs.define`` / ``@dataclass`` so it runs *first*
    (decorators apply bottom-up): the annotation must exist before attrs /
    dataclasses scan the body and turn it into a real field / slot::

        @define
        @add_field
        class Box[T]:
            ...

    Storage is a real field, so it survives ``attrs.evolve`` / ``copy`` /
    ``pickle``. No-op if ``__orig_class__`` is already declared (own
    annotations, a ``__slots__`` entry, or an attrs/dataclass field).
    """
    own_ann = cls.__dict__.get("__annotations__")
    if (own_ann and "__orig_class__" in own_ann) or _orig_class_declared(cls):
        return cls
    if own_ann is None:
        cls.__annotations__ = own_ann = {}
    own_ann["__orig_class__"] = type | None
    cls.__orig_class__ = None  # type: ignore[attr-defined]
    return cls


def get_orig_class(instance: object) -> Any | None:
    """The alias ``instance`` was constructed through, by any strategy:

    1. ``__orig_class__`` (native ``__dict__`` storage, or a declared field);
    2. ``_paramsight_alias`` on the synthetic class (``class_swap``);
    3. the ``id``-keyed side table (``side_table``).
    """
    orig = getattr(instance, "__orig_class__", None)
    if orig is not None:
        return orig
    alias = getattr(type(instance), "_paramsight_alias", None)
    if alias is not None:
        return alias
    return side_table.lookup(instance)
