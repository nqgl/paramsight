"""``side_table`` strategy: out-of-band ``id`` -> alias tracking.

No field pollution. Does NOT survive ``attrs.evolve`` / ``copy`` / ``pickle``
(those produce fresh instances absent from the table). Requires weakref-able
instances (attrs ``@define`` / ``@frozen`` by default; ``@dataclass(slots=True)``
needs ``weakref_slot=True``).
"""

import weakref
from typing import Any

# ``id(instance) -> alias``. Entries are removed by a ``weakref.finalize``
# registered in ``remember_orig_class`` (see the id-reuse note there).
# Individual dict ops (``__setitem__`` from remember, ``pop`` from the
# finalizer) are atomic under CPython's GIL and there is no compound
# read-modify-write, so this is safe in normal CPython; under free-threading
# the usual caveats apply (mirrors the library's existing multithreading note
# for super-injection).
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


def lookup(instance: object) -> Any | None:
    """The recorded alias for ``instance``, or ``None``."""
    return _alias_by_id.get(id(instance))


def uses_side_table(cls: type) -> type:
    """Class decorator: track parametrization of this slotted class's instances
    out of band (``id``-keyed, weakref-cleaned).

    No field pollution. Does NOT survive ``attrs.evolve`` / ``copy`` /
    ``pickle``. Requires weakref-able instances (attrs ``@define`` / ``@frozen``
    by default; ``@dataclass(slots=True)`` needs ``weakref_slot=True``).
    Equivalent to ``_paramsight_slots = "side_table"`` in the class body.
    """
    cls._paramsight_slots = "side_table"  # type: ignore[attr-defined]
    return cls
