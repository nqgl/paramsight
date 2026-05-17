"""Slotted-class handling for ``@takes_alias`` / ``TypeVarValue``.

A slotted class (``attrs.define`` / ``@frozen`` / ``@dataclass(slots=True)`` /
manual ``__slots__``) has no ``__dict__``, so CPython's
``result.__orig_class__ = alias`` from the generic alias call silently fails.
paramsight refuses to silently drop the parametrization -- but rather than
failing at class-creation time (which would punish factory-only patterns that
never actually need to recover the alias from an instance), it raises only at
the point where the silent failure would matter: when an instance-level
``@takes_alias``/``TypeVarValue`` ``__get__`` couldn't recover the alias
and the class has no storage for ``__orig_class__`` and no opt-in.

Users provide one of:

- a real field (``__orig_class__: type | None = None`` declared in the body,
  or via ``attrs.field``/``dataclasses.field`` to tune semantics) -- survives
  ``attrs.evolve`` / ``copy`` / ``pickle``;
- ``_paramsight_slots = "side_table"`` -- out-of-band tracking (no field
  pollution; does NOT survive ``evolve`` / ``copy`` / ``pickle``; needs
  weakref-able instances).
"""

import copy
import inspect
import pickle
from dataclasses import dataclass
from dataclasses import fields as dc_fields

import attrs
import pytest
from attrs import define, frozen

from paramsight import get_args_at_base, takes_alias

# ---------------------------------------------------------------------------
# The deferred raise: instance lookup is the trigger, not class creation.
# ---------------------------------------------------------------------------


def test_slotted_class_with_no_storage_defines_fine_when_only_class_side_use():
    # Factory-only pattern: never look ``f`` up on an instance, so we don't
    # need __orig_class__ storage. paramsight must NOT raise at class creation.
    @define
    class Factory[T]:
        @takes_alias
        @classmethod
        def make(cls):
            return get_args_at_base(cls, Factory)

    assert Factory[int].make() == (int,)


def test_attrs_define_instance_lookup_without_storage_raises():
    @define
    class A[T]:
        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    # Class-side: fine.
    assert A[int].f() == (int,)
    # Instance-side: raises with guidance.
    inst = A[int]()
    with pytest.raises(TypeError, match=r"_paramsight_slots|__orig_class__"):
        inst.f()


def test_attrs_frozen_instance_lookup_without_storage_raises():
    @frozen
    class A[T]:
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    with pytest.raises(TypeError, match=r"_paramsight_slots|__orig_class__"):
        A[int](x=1).f()


def test_dataclass_slots_instance_lookup_without_storage_raises():
    @dataclass(slots=True)
    class A[T]:
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    with pytest.raises(TypeError, match=r"_paramsight_slots|__orig_class__"):
        A[int](x=1).f()


def test_manual_slots_instance_lookup_without_storage_raises():
    class A[T]:
        __slots__ = ("x",)

        def __init__(self, x):
            self.x = x

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    with pytest.raises(TypeError, match=r"_paramsight_slots|__orig_class__"):
        A[int](x=1).f()


def test_unrecognised_strategy_raises_value_error_at_first_use():
    @define
    class A[T]:
        _paramsight_slots = "not_a_strategy"

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    with pytest.raises(ValueError, match=r"_paramsight_slots"):
        A[int]()


def test_non_slotted_dataclass_needs_no_strategy():
    # Non-slotted classes have ``__dict__``; CPython stamps ``__orig_class__``
    # natively. No strategy or declaration required.
    @dataclass
    class A[T]:
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    assert A[int].f() == (int,)
    assert A[int](x=1).f() == (int,)


# ---------------------------------------------------------------------------
# Strategy: ``_paramsight_slots = "side_table"``
# ---------------------------------------------------------------------------


@define
class SideTableBox[T]:
    _paramsight_slots = "side_table"
    x: int = 0

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SideTableBox)


def test_side_table_no_field_pollution():
    assert "__orig_class__" not in {f.name for f in attrs.fields(SideTableBox)}
    assert "__orig_class__" not in SideTableBox.__slots__
    sig = inspect.signature(SideTableBox.__init__)
    assert not any("orig_class" in p for p in sig.parameters)
    assert "__orig_class__" not in attrs.asdict(SideTableBox[int](x=1))
    assert "orig_class" not in repr(SideTableBox[int](x=1))


def test_side_table_resolves_on_class_and_instance():
    assert SideTableBox[int].get_type() == (int,)
    assert SideTableBox[int](x=1).get_type() == (int,)


def test_side_table_distinct_parametrizations_dont_bleed():
    a = SideTableBox[int](x=1)
    b = SideTableBox[str](x=2)
    assert a.get_type() == (int,)
    assert b.get_type() == (str,)


def test_side_table_does_not_survive_evolve_or_copy():
    """Documented limitation of side_table: fresh instances aren't in the
    table. (Declare ``__orig_class__`` yourself if you need evolve/copy/pickle
    preservation.)"""
    inst = SideTableBox[int](x=1)
    assert inst.get_type() == (int,)
    # evolve / copy produce a fresh instance with no side-table entry;
    # opt-in means we fall back silently (rather than raising) for the miss.
    assert attrs.evolve(inst, x=2).get_type() != (int,)
    assert copy.copy(inst).get_type() != (int,)
    assert copy.deepcopy(inst).get_type() != (int,)


def test_side_table_unparametrized_instance_falls_back_silently():
    # ``SideTableBox()`` (no [T]) is a legit unparametrized instance under the
    # opt-in -- callers accept that typevars resolve against the bare class.
    assert SideTableBox(x=1).get_type() != (int,)


def test_side_table_raises_loudly_when_instance_not_weakrefable():
    @dataclass(slots=True)  # default weakref_slot=False -> not weakref-able
    class A[T]:
        _paramsight_slots = "side_table"
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    with pytest.raises(TypeError, match=r"not weakref-able"):
        A[int](x=1)


# ---------------------------------------------------------------------------
# Declared ``__orig_class__`` field: user opts in by writing it themselves.
# This is the recommended path for evolve/copy/pickle preservation, since the
# user controls ``field(...)`` semantics directly.
# ---------------------------------------------------------------------------


@define
class FieldBox[T]:
    __orig_class__: type | None = None
    x: int = 0

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, FieldBox)


def test_declared_field_resolves_on_class_and_instance():
    assert FieldBox[int].get_type() == (int,)
    assert FieldBox[int](x=1).get_type() == (int,)


def test_declared_field_survives_evolve_copy_deepcopy():
    inst = FieldBox[int](x=1)
    assert attrs.evolve(inst, x=2).get_type() == (int,)
    assert copy.copy(inst).get_type() == (int,)
    assert copy.deepcopy(inst).get_type() == (int,)


def test_declared_field_survives_pickle():
    inst = FieldBox[int](x=1)
    revived = pickle.loads(pickle.dumps(inst))
    assert revived.get_type() == (int,)


def test_declared_field_on_frozen_uses_object_setattr_bypass():
    # Frozen attrs' __setattr__ rejects ``inst.__orig_class__ = alias``;
    # paramsight bypasses via ``object.__setattr__`` when the class has
    # ``__orig_class__`` storage.
    @frozen
    class FrozenFieldBox[T]:
        __orig_class__: type | None = None
        x: int = 0

        @takes_alias
        @classmethod
        def get_type(cls):
            return get_args_at_base(cls, FrozenFieldBox)

    inst = FrozenFieldBox[int](x=1)
    assert inst.get_type() == (int,)
    assert attrs.evolve(inst, x=2).get_type() == (int,)


def test_declared_field_with_attrs_field_can_hide_from_repr_and_eq():
    @define
    class A[T]:
        __orig_class__: type | None = attrs.field(
            default=None, repr=False, eq=False
        )
        x: int = 0

        @takes_alias
        @classmethod
        def get_type(cls):
            return get_args_at_base(cls, A)

    # repr: hidden
    assert "orig_class" not in repr(A[int](x=1))
    # eq: doesn't differ across parametrizations
    assert A[int](x=1) == A[str](x=1)
    # but still works for resolution
    assert A[int](x=1).get_type() == (int,)
    assert attrs.evolve(A[int](x=1), x=2).get_type() == (int,)


# ---------------------------------------------------------------------------
# Inheritance: a child inherits the parent's marker / declared field via MRO.
# ---------------------------------------------------------------------------


def test_attrs_slotted_subclass_inherits_strategy_from_base():
    @define
    class Base[T]:
        _paramsight_slots = "side_table"

        @takes_alias
        @classmethod
        def base(cls):
            return get_args_at_base(cls, Base)

    @define
    class Leaf[T](Base[T]):
        x: int = 0

    assert Leaf[int].base() == (int,)
    assert Leaf[int](x=1).base() == (int,)


def test_dataclass_subclass_can_add_non_default_field_under_side_table():
    @dataclass
    class Parent[T]:
        _paramsight_slots = "side_table"

        @takes_alias
        @classmethod
        def base(cls):
            return get_args_at_base(cls, Parent)

    @dataclass
    class Child[T](Parent[T]):
        y: int

    assert [f.name for f in dc_fields(Child)] == ["y"]
    assert Child[int](y=5).y == 5
    assert Child[int](y=5).base() == (int,)


# ---------------------------------------------------------------------------
# Internal: __class_getitem__ doesn't get wrapped around itself across the
# attrs slots-rebuild.
# ---------------------------------------------------------------------------


def test_attrs_slots_rebuild_does_not_double_wrap_class_getitem():
    cgi = inspect.getattr_static(SideTableBox, "__class_getitem__")
    if isinstance(cgi, classmethod):
        cgi = cgi.__func__
    inner = inspect.unwrap(cgi)
    assert inner.__name__ == "_patched_cgi"
    bases = [c.cell_contents for c in (inner.__closure__ or ())]
    assert not any(
        getattr(inspect.unwrap(b), "__name__", None) == "_patched_cgi"
        for b in bases
        if callable(b)
    ), "patched __class_getitem__ was wrapped around itself"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
