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
- ``_paramsight_slots = "side_table"`` (or ``@uses_side_table``) -- out-of-band
  tracking (no field pollution; does NOT survive ``evolve`` / ``copy`` /
  ``pickle``; needs weakref-able instances);
- ``_paramsight_slots = "class_swap"`` (or ``@uses_class_swap``) -- per-
  parametrization synthetic subclass (no field pollution; survives ``evolve``
  / ``copy`` / ``pickle``; ``type(inst) is Cls`` becomes False).
"""

import copy
import inspect
import pickle
from dataclasses import dataclass
from dataclasses import fields as dc_fields

import attrs
import pytest
from attrs import define, frozen

from paramsight import TypeVarValue, get_args_at_base, takes_alias
from paramsight.slotted_strategies import (
    add_field,
    uses_class_swap,
    uses_side_table,
)

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
# The raise must NOT fire when there is nothing to recover: a slotted class
# with no *free* typevars (a non-generic class, or a concrete subclass that
# already binds the base's typevars via its MRO) resolves fine straight from
# ``type(instance)`` -- no instance alias is needed.
# ---------------------------------------------------------------------------


def test_non_generic_slotted_class_instance_lookup_does_not_raise():
    # A non-generic ``@define`` class has no typevars, so there is no
    # parametrization to lose. Instance-side ``@takes_alias`` must fall back to
    # the class, not raise the slotted-without-storage error.
    @define
    class A:
        @takes_alias
        @classmethod
        def f(cls):
            return cls

    assert A.f() is A
    assert A().f() is A


def test_concrete_slotted_subclass_resolves_from_mro_on_instance():
    # ``Concrete(Base[int])`` encodes ``int`` in its MRO (``__orig_bases__``);
    # an instance carries no alias of its own and needs none. Instance-side
    # lookup must resolve against ``type(instance)``, not raise.
    @define
    class Base[T]:
        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, Base)

    @define
    class Concrete(Base[int]):
        pass

    assert Concrete.f() == (int,)
    assert Concrete().f() == (int,)


def test_concrete_slotted_subclass_resolves_typevarvalue_on_instance():
    # Same edge case via the ``TypeVarValue`` descriptor -- the fix gates both
    # instance-side ``__get__`` sites.
    @define
    class Base[T]:
        value_type = TypeVarValue[T]()

    @define
    class Concrete(Base[int]):
        pass

    assert Concrete.value_type is int
    assert Concrete().value_type is int


def test_slotted_subclass_with_free_typevar_still_raises():
    # Regression guard: a subclass that still has a *free* typevar genuinely
    # loses its parametrization when a slotted instance has no storage, so the
    # raise must still fire here.
    @define
    class Base[T]:
        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, Base)

    @define
    class Partial[U](Base[U]):
        pass

    assert Partial[int].f() == (int,)
    with pytest.raises(TypeError, match=r"_paramsight_slots|__orig_class__"):
        Partial[int]().f()


def test_slotted_subclass_binding_base_typevar_but_adding_own_still_raises():
    # ``Sub[U](Base[int])`` binds ``Base``'s typevar (recoverable from the MRO)
    # but adds its *own* free ``U``. An instance built as ``Sub[str]()`` really
    # carried ``U=str``; a slotted instance with no storage drops it, so a
    # consumer that resolves ``Sub``'s typevars would silently get ``NoDefault``.
    # The raise must still fire -- even though the method below only needs
    # ``Base``'s (recoverable) typevar, we cannot assume callers stop there.
    @define
    class Base[T]:
        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, Base)

    @define
    class Sub[U](Base[int]):
        pass

    assert Sub[str].f() == (int,)  # class-side: fine
    with pytest.raises(TypeError, match=r"_paramsight_slots|__orig_class__"):
        Sub[str]().f()


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
        __orig_class__: type | None = attrs.field(default=None, repr=False, eq=False)
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


# ---------------------------------------------------------------------------
# Strategy: ``class_swap`` (synthetic per-parametrization subclass)
# ---------------------------------------------------------------------------


@uses_class_swap
@frozen
class SwapBox[T]:
    x: int = 0

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SwapBox)


@uses_class_swap
class SwapPair[T]:
    # A class_swap class whose ``__new__`` *requires* arguments, exposed for
    # reconstruction via ``__getnewargs__``. Module-level so pickle can find it.
    __slots__ = ("a", "b")

    def __new__(cls, a, b):
        return object.__new__(cls)

    def __init__(self, a, b):
        self.a, self.b = a, b

    def __getnewargs__(self):
        return (self.a, self.b)

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SwapPair)


def test_class_swap_resolves_on_class_and_instance():
    assert SwapBox[int].get_type() == (int,)
    assert SwapBox[int](x=1).get_type() == (int,)


def test_class_swap_no_field_pollution():
    assert "__orig_class__" not in {f.name for f in attrs.fields(SwapBox)}
    assert "orig_class" not in repr(SwapBox[int](x=1))
    assert "__orig_class__" not in attrs.asdict(SwapBox[int](x=1))


def test_class_swap_isinstance_true_but_type_identity_changes():
    inst = SwapBox[int](x=1)
    assert isinstance(inst, SwapBox)  # subclass
    assert type(inst) is not SwapBox  # the documented trade-off
    assert type(inst).__name__ == "SwapBox"  # repr stays sane


def test_class_swap_distinct_parametrizations_dont_bleed():
    assert SwapBox[int](x=1).get_type() == (int,)
    assert SwapBox[str](x=2).get_type() == (str,)
    # same parametrization -> same synthetic class (stable identity)
    assert type(SwapBox[int](x=1)) is type(SwapBox[int](x=9))


def test_class_swap_survives_evolve_copy_deepcopy_pickle():
    inst = SwapBox[int](x=1)
    assert attrs.evolve(inst, x=2).get_type() == (int,)
    assert copy.copy(inst).get_type() == (int,)
    assert copy.deepcopy(inst).get_type() == (int,)
    revived = pickle.loads(pickle.dumps(inst))
    assert revived.get_type() == (int,)
    assert revived.x == 1
    assert isinstance(revived, SwapBox)


def test_class_swap_copy_pickle_with_arg_taking_new():
    # Regression: copy/pickle reconstruction used ``origin.__new__(origin)`` with
    # no args, ignoring ``__getnewargs__`` / an arg-requiring ``__new__`` -- so
    # these raised ``TypeError`` despite class_swap advertising copy/pickle
    # survival. They must now round-trip, preserving parametrization and state.
    inst = SwapPair[int](1, 2)
    assert inst.get_type() == (int,)
    for revived in (
        copy.copy(inst),
        copy.deepcopy(inst),
        pickle.loads(pickle.dumps(inst)),
    ):
        assert revived.get_type() == (int,)
        assert (revived.a, revived.b) == (1, 2)
        assert isinstance(revived, SwapPair)


@uses_class_swap
class SwapCustomReduceEx[T]:
    # A class bringing its own ``__reduce_ex__`` (V6): pickle/copy call it
    # *before* ``__reduce__``, so paramsight must honor it -- it controls
    # reconstruction -- and re-attach the parametrization around its payload.
    # Module-level so pickle can find it.
    __slots__ = ("x",)

    def __init__(self, x):
        self.x = x

    def __reduce_ex__(self, protocol):
        return (SwapCustomReduceEx, (self.x,))

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SwapCustomReduceEx)


@uses_class_swap
class SwapCustomReduce[T]:
    # Same for a custom ``__reduce__`` (without ``__reduce_ex__``), which the
    # synthetic's own ``__reduce__`` would otherwise shadow outright.
    __slots__ = ("x",)

    def __init__(self, x):
        self.x = x

    def __reduce__(self):
        return (SwapCustomReduce, (self.x,))

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SwapCustomReduce)


@uses_class_swap
class SwapSuperReduceEx[T]:
    # A delegating ``__reduce_ex__`` (calls the default machinery via super())
    # must compose: the default machinery dispatches to the synthetic's
    # ``__reduce__``, and the shim wraps that payload a second time.
    __slots__ = ("x",)

    def __init__(self, x):
        self.x = x

    def __reduce_ex__(self, protocol):
        return super().__reduce_ex__(protocol)

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SwapSuperReduceEx)


def test_class_swap_custom_reduce_ex_keeps_parametrization():
    # Regression (V6): a custom ``__reduce_ex__`` bypassed the synthetic's
    # ``__reduce__``, so the revived instance was a bare origin -- typevar
    # lookups silently resolved to NoDefault instead of the bound type.
    inst = SwapCustomReduceEx[int](5)
    assert inst.get_type() == (int,)
    for revived in (
        copy.copy(inst),
        copy.deepcopy(inst),
        pickle.loads(pickle.dumps(inst)),
    ):
        assert revived.get_type() == (int,)
        assert revived.x == 5
        assert isinstance(revived, SwapCustomReduceEx)


def test_class_swap_custom_reduce_keeps_parametrization():
    inst = SwapCustomReduce[str](7)
    for revived in (
        copy.copy(inst),
        copy.deepcopy(inst),
        pickle.loads(pickle.dumps(inst)),
    ):
        assert revived.get_type() == (str,)
        assert revived.x == 7
        assert isinstance(revived, SwapCustomReduce)


def test_class_swap_delegating_reduce_ex_round_trips():
    inst = SwapSuperReduceEx[bytes](9)
    for revived in (
        copy.copy(inst),
        copy.deepcopy(inst),
        pickle.loads(pickle.dumps(inst)),
    ):
        assert revived.get_type() == (bytes,)
        assert revived.x == 9


@uses_class_swap
class SwapFactoryBase[T]:
    # A factory-style ``__new__`` returning an instance of a *subclass* (V7).
    # The swap must preserve the returned identity -- including a subclass that
    # adds slots, which is layout-incompatible with the base itself.
    __slots__ = ("a",)

    def __new__(cls, *args, **kwargs):
        if cls is SwapFactoryBase:
            return object.__new__(SwapFactorySub)
        return object.__new__(cls)

    def __init__(self, a):
        self.a = a

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, SwapFactoryBase)


class SwapFactorySub(SwapFactoryBase):
    __slots__ = ("b",)  # extra slot: layout differs from the base


def test_class_swap_foreign_subclass_new_preserves_identity():
    # Regression (V7): the swap targeted a synthetic of the *origin*, which
    # raised "object layout differs" for a slot-adding subclass (and silently
    # stripped the identity of a layout-compatible one). The synthetic must be
    # built over the class the constructor actually returned.
    inst = SwapFactoryBase[int](1)
    assert isinstance(inst, SwapFactorySub)  # identity preserved
    assert inst.get_type() == (int,)  # parametrization tracked
    assert inst.a == 1


def test_class_swap_foreign_subclass_survives_copy_pickle():
    inst = SwapFactoryBase[int](1)
    for revived in (
        copy.copy(inst),
        copy.deepcopy(inst),
        pickle.loads(pickle.dumps(inst)),
    ):
        assert isinstance(revived, SwapFactorySub)
        assert revived.get_type() == (int,)
        assert revived.a == 1


def test_class_swap_on_dataclass_slots_and_manual_slots():
    @uses_class_swap
    @dataclass(slots=True)
    class DC[T]:
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, DC)

    assert DC[int](x=1).f() == (int,)
    assert copy.deepcopy(DC[int](x=5)).f() == (int,)

    @uses_class_swap
    class Manual[T]:
        __slots__ = ("x",)

        def __init__(self, x=0):
            self.x = x

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, Manual)

    assert Manual[int](1).f() == (int,)
    # (pickle needs a module-level class -- covered by SwapBox above)
    assert copy.deepcopy(Manual[int](3)).f() == (int,)


def test_class_swap_suppresses_init_subclass_for_synthetics():
    calls = []

    class Hook:
        def __init_subclass__(cls, **kw):
            super().__init_subclass__(**kw)
            calls.append(cls.__name__)

    @uses_class_swap
    @define
    class Child[T](Hook):
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, Child)

    calls.clear()
    # Building synthetics for these parametrizations must NOT fire Hook's
    # __init_subclass__ (a synthetic is an implementation detail).
    Child[int](x=1).f()
    Child[str](x=2).f()
    assert calls == []


def test_class_swap_unparametrized_instance_falls_back_silently():
    # ``SwapBox()`` (no [T]) is a legit unparametrized instance: type is the
    # real class, no synthetic, resolution falls back to the bare class.
    inst = SwapBox(x=1)
    assert type(inst) is SwapBox
    assert inst.get_type() != (int,)


# ---------------------------------------------------------------------------
# Decorators are equivalent to the class-body marker.
# ---------------------------------------------------------------------------


def test_uses_side_table_decorator_equivalent_to_marker():
    @uses_side_table
    @define
    class A[T]:
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, A)

    assert A._paramsight_slots == "side_table"
    assert A[int](x=1).f() == (int,)


def test_uses_class_swap_decorator_sets_marker():
    assert SwapBox._paramsight_slots == "class_swap"


# ---------------------------------------------------------------------------
# @add_field: decorator-injected real ``__orig_class__`` field.
# ---------------------------------------------------------------------------


@define
@add_field
class AddFieldBox[T]:
    x: int = 0

    @takes_alias
    @classmethod
    def f(cls):
        return get_args_at_base(cls, AddFieldBox)


def test_add_field_makes_a_real_field_resolvable_instance_side():
    assert "__orig_class__" in {a.name for a in attrs.fields(AddFieldBox)}
    assert AddFieldBox[int](x=1).f() == (int,)


def test_add_field_survives_evolve_copy_deepcopy_pickle():
    inst = AddFieldBox[int](x=1)
    assert attrs.evolve(inst).f() == (int,)
    assert copy.copy(inst).f() == (int,)
    assert copy.deepcopy(inst).f() == (int,)
    assert pickle.loads(pickle.dumps(inst)).f() == (int,)


def test_add_field_is_noop_when_already_declared():
    @define
    @add_field
    class B[T]:
        __orig_class__: type | None = None

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, B)

    names = [a.name for a in attrs.fields(B)]
    assert names.count("__orig_class__") == 1  # not double-added
    assert B[str]().f() == (str,)


def test_add_field_on_dataclass_slots():
    @dataclass(slots=True)
    @add_field
    class DC[T]:
        x: int = 0

        @takes_alias
        @classmethod
        def f(cls):
            return get_args_at_base(cls, DC)

    assert "__orig_class__" in {f.name for f in dc_fields(DC)}
    assert DC[int](x=1).f() == (int,)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
