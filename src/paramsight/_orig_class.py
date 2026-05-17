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

When instance-side use is intended, the user provides one of:

- A real field (recommended). Declare ``__orig_class__: type | None = None``
  in the class body, or use ``attrs.field(...)`` / ``dataclasses.field(...)``
  to tune ``repr`` / ``eq`` / ``init`` / ``kw_only``. Survives
  ``attrs.evolve`` / ``copy`` / ``pickle``. ``_GAProxy.__call__`` uses
  ``object.__setattr__`` to populate it, which bypasses frozen attrs'
  ``__setattr__``.

- ``_paramsight_slots = "side_table"``. Out-of-band tracking via this module.
  Keyed by ``id`` with a ``weakref.finalize`` cleanup -- works whenever the
  instance is weakref-able (attrs ``@define`` / ``@frozen`` by default;
  ``@dataclass(slots=True)`` needs ``weakref_slot=True``). Does NOT survive
  ``attrs.evolve`` / ``copy`` / ``pickle``: those produce fresh instances
  absent from the table. ``_GAProxy.__call__`` raises loudly at construction
  if the instance can't be tracked at all (not weakref-able), to avoid the
  silent-untracked trap.
"""

import weakref
from typing import Any


def _is_effectively_slotted(cls: type) -> bool:
    """True if instances of ``cls`` have no ``__dict__`` -- i.e. arbitrary
    attribute assignment fails.

    Uses CPython's ``__dictoffset__``: 0 means "no per-instance ``__dict__``."
    (Walking the MRO looking for missing ``__slots__`` is *not* equivalent --
    e.g. ``typing.Generic`` declares no ``__slots__`` yet contributes no
    ``__dict__``, because its ``__dictoffset__`` is 0.)
    """
    return getattr(cls, "__dictoffset__", -1) == 0


def _has_orig_class_storage(cls: type) -> bool:
    """True if instances of ``cls`` can carry ``__orig_class__``: either via
    ``__dict__``, a ``__slots__`` entry, or an attrs/dataclass field."""
    if not _is_effectively_slotted(cls):
        return True
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


_VALID_SLOT_STRATEGIES = frozenset({"side_table"})


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


# Out-of-band ``id(instance) -> alias`` table for the "side_table" strategy.
# Entries are removed by a ``weakref.finalize`` registered in
# ``remember_orig_class`` (see the id-reuse note there). Individual dict ops
# (``__setitem__`` from remember, ``pop`` from the finalizer) are atomic under
# CPython's GIL and there is no compound read-modify-write, so this is safe in
# normal CPython; under free-threading the usual caveats apply (mirrors the
# library's existing multithreading note for super-injection).
_alias_by_id: dict[int, Any] = {}


def remember_orig_class(instance: object, alias: Any) -> bool:
    """Record ``alias`` as the parametrization of ``instance``.

    Returns ``True`` on success, ``False`` if the instance can't be tracked
    (not weakref-able -- without a finalizer we can't know when its ``id`` is
    free to reuse, so caching could hand a later object the wrong alias).
    """
    key = id(instance)
    try:
        weakref.finalize(instance, _alias_by_id.pop, key, None)
    except TypeError:
        return False
    _alias_by_id[key] = alias
    return True


def get_orig_class(instance: object) -> Any | None:
    """``instance.__orig_class__`` if present, else any side-table record."""
    orig = getattr(instance, "__orig_class__", None)
    if orig is not None:
        return orig
    return _alias_by_id.get(id(instance))
