"""Tests for the ``TypeVarValue`` descriptor.

Covers runtime resolution and (via ``typing.assert_type``, which is a runtime
no-op but checked by static type checkers) the narrowed ``type[T]`` surface.
"""

from collections.abc import Callable
from types import GenericAlias, NoneType
from typing import (
    Annotated,
    ForwardRef,
    Generic,
    TypeVar,
    assert_type,
    get_args,
    get_origin,
)

import pytest
from pydantic import BaseModel, ConfigDict

from paramsight import (
    TypeVarValue,
    TypeVarValueOption,
    get_args_at_base,
    takes_alias,
)

# ---------------------------------------------------------------------------
# Basic
# ---------------------------------------------------------------------------


class Box[T]:
    value_type = TypeVarValue[T]()


def test_basic_subscript():
    assert Box[int].value_type is int
    assert Box[str].value_type is str
    assert_type(Box[int].value_type, type[int])
    assert_type(Box[str].value_type, type[str])


def test_distinct_aliases_do_not_interfere():
    # Touch several specializations in sequence; each must keep its own answer.
    assert Box[int].value_type is int
    assert Box[str].value_type is str
    assert Box[bytes].value_type is bytes
    assert Box[int].value_type is int


def test_generic_alias_argument():
    assert Box[list[int]].value_type == list[int]
    assert Box[dict[str, int]].value_type == dict[str, int]


# ---------------------------------------------------------------------------
# Instance access
# ---------------------------------------------------------------------------


def test_instance_access_via_alias():
    assert Box[int]().value_type is int
    b = Box[str]()
    assert b.value_type is str
    assert_type(b.value_type, type[str])


class IntBox(Box[int]): ...


def test_instance_access_via_concrete_subclass():
    assert IntBox().value_type is int
    assert IntBox.value_type is int


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


class Child[U](Box[U]): ...


def test_inheritance_specialized_alias():
    assert Child[str].value_type is str
    assert_type(Child[str].value_type, type[str])


class GrandChild(Child[bool]): ...


def test_inheritance_concrete_subclass():
    assert GrandChild.value_type is bool
    assert GrandChild().value_type is bool


# ---------------------------------------------------------------------------
# Multiple typevars on one class
# ---------------------------------------------------------------------------


class Pair[A, B]:
    first_type = TypeVarValue[A]()
    second_type = TypeVarValue[B]()


def test_multiple_typevars_resolve_independently():
    assert Pair[int, str].first_type is int
    assert Pair[int, str].second_type is str
    assert_type(Pair[int, str].first_type, type[int])
    assert_type(Pair[int, str].second_type, type[str])


# ---------------------------------------------------------------------------
# TypeVar default
# ---------------------------------------------------------------------------


class Defaulted[T = int]:
    value_type = TypeVarValue[T]()


def test_typevar_default_used_when_unspecialized():
    assert Defaulted.value_type is int


def test_typevar_default_overridden_when_specialized():
    assert Defaulted[str].value_type is str


# ---------------------------------------------------------------------------
# Default coercion -- a default resolves the same as an explicit argument.
# ``T = None`` is the motivating case: the raw ``__default__`` is the ``None``
# singleton, but subscription turns ``C[None]`` into ``NoneType``, so the
# default branch must agree (``None`` -> ``NoneType``), not leak the singleton.
# ---------------------------------------------------------------------------


class DefaultedNone[T = None]:
    value_type = TypeVarValue[T]()


def test_none_default_coerces_to_nonetype():
    assert DefaultedNone.value_type is NoneType


def test_none_default_branch_agrees_with_explicit_none():
    # The whole point of the coercion: C and C[None] must not disagree.
    assert DefaultedNone.value_type is DefaultedNone[None].value_type is NoneType


class _FwdTarget: ...


class DefaultedForwardRef[T = "_FwdTarget"]:
    value_type = TypeVarValue[T]()


def test_forward_ref_default_coerces_like_subscription():
    # General coercion, not a None special-case: a forward-ref default is stored
    # as the bare string ``"_FwdTarget"`` but must surface as a ForwardRef, the
    # same as subscription -- and the two branches must agree.
    v = DefaultedForwardRef.value_type
    assert isinstance(v, ForwardRef)
    assert v.__forward_arg__ == "_FwdTarget"
    assert v == DefaultedForwardRef["_FwdTarget"].value_type


# ---------------------------------------------------------------------------
# TypeVarValue (surface B): unbound + no default is an *error*, not a value.
# ---------------------------------------------------------------------------


class NoDefault[T]:
    value_type = TypeVarValue[T]()


def test_unbound_no_default_raises():
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        NoDefault.value_type


def test_unbound_no_default_still_resolves_when_specialized():
    assert NoDefault[int].value_type is int


# ---------------------------------------------------------------------------
# Defaults that *reference another typevar*. PEP 696 lets a default name an
# earlier type parameter (``class C[T, U = T]``), and Python auto-fills that
# default into ``__args__`` as the raw, unsubstituted typevar -- ``C[int]`` has
# args ``(int, T)``. Resolution must carry the ``T -> int`` binding into ``U``,
# rather than leak the raw typevar (which would also slip past the unresolved
# guard, since a free TypeVar is neither NoDefault nor a concrete type).
# ---------------------------------------------------------------------------


class SelfRef[T, U = T]:
    u_type = TypeVarValue[U]()
    u_opt = TypeVarValueOption[U]()


def test_typevar_referencing_default_resolves_through_binding():
    assert SelfRef[int].u_type is int
    assert get_args_at_base(SelfRef[int], SelfRef) == (int, int)


def test_typevar_referencing_default_unresolved_when_bare():
    # T is itself free here, so U = T has nothing concrete to become.
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        SelfRef.u_type
    assert SelfRef.u_opt is None


class NestedSelfRef[T, U = list[T]]:
    u_type = TypeVarValue[U]()
    u_opt = TypeVarValueOption[U]()


def test_nested_typevar_referencing_default_resolves():
    assert NestedSelfRef[int].u_type == list[int]
    assert get_args_at_base(NestedSelfRef[int], NestedSelfRef) == (int, list[int])


def test_nested_typevar_referencing_default_unresolved_when_bare():
    # Bare access leaves a hole: list[<unresolved>]. The unresolved check is
    # recursive, so this is caught even though the top-level value is a list.
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        NestedSelfRef.u_type
    assert NestedSelfRef.u_opt is None


# ---------------------------------------------------------------------------
# A free typevar surviving through a base's generic argument -- the resolved
# value is a *partial* (``list[<unresolved>]``), which the recursive check must
# also treat as unresolved rather than hand back.
# ---------------------------------------------------------------------------


class _PartialBase[X]:
    x_type = TypeVarValue[X]()
    x_opt = TypeVarValueOption[X]()


class _PartialMid[T](_PartialBase[list[T]]): ...


def test_partial_resolution_through_base_is_unresolved():
    assert _PartialMid[int].x_type == list[int]
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        _PartialMid.x_type
    assert _PartialMid.x_opt is None


# ---------------------------------------------------------------------------
# Unions. A ``T`` inside ``int | T`` is a ``types.UnionType``, not a generic
# alias, so it needs its own walk both for substitution and for the unresolved
# check. (Substituting into a union also can't go via ``__class_getitem__``.)
# ---------------------------------------------------------------------------


class UnionDefault[T, U = int | T]:
    u_type = TypeVarValue[U]()
    u_opt = TypeVarValueOption[U]()


def test_union_default_referencing_typevar_resolves():
    assert UnionDefault[str].u_type == int | str
    assert get_args_at_base(UnionDefault[str], UnionDefault) == (str, int | str)


def test_union_default_unresolved_when_bare():
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        UnionDefault.u_type
    assert UnionDefault.u_opt is None


class _UnionBase[X]:
    x_type = TypeVarValue[X]()


class _UnionMid[T](_UnionBase[int | T]): ...


def test_union_argument_through_base_resolves():
    # Pre-existing resolution gap, independent of defaults: a union arg passed to
    # a base must carry the binding into the union members.
    assert _UnionMid[str].x_type == int | str


# ---------------------------------------------------------------------------
# Typing special forms. ``Callable`` nests its parameter list (and base-class
# resolution yields the ``collections.abc`` spelling, which has no ``copy_with``),
# while forms like ``Annotated`` have non-class origins. Each is reconstructed,
# and a free typevar *inside* one must still register as unresolved -- including
# inside a ``Callable``'s plain-``list`` parameter argument.
# ---------------------------------------------------------------------------


def _is_callable_of(value, params, ret):
    # Spelling-agnostic: typing.Callable != collections.abc.Callable by ``==``,
    # so compare structurally.
    return get_origin(value) is Callable and get_args(value) == (params, ret)


class _CallableBase[X]:
    x_type = TypeVarValue[X]()
    x_opt = TypeVarValueOption[X]()


class _CallableMid[T](_CallableBase[Callable[[T], int]]): ...


def test_callable_argument_resolves_through_parameter_list():
    assert _is_callable_of(_CallableMid[str].x_type, [str], int)


def test_callable_with_free_typevar_is_unresolved():
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        _CallableMid.x_type
    assert _CallableMid.x_opt is None


class _CallableDefault[T, U = Callable[[T], int]]:
    u_type = TypeVarValue[U]()


def test_callable_typevar_default_resolves():
    assert _is_callable_of(_CallableDefault[str].u_type, [str], int)


class _AnnotatedBase[X]:
    x_type = TypeVarValue[X]()


class _AnnotatedMid[T](_AnnotatedBase[Annotated[T, "meta"]]): ...


def test_annotated_argument_resolves_and_keeps_metadata():
    assert _AnnotatedMid[int].x_type == Annotated[int, "meta"]


# A PEP 695 generic ``type`` alias has a non-class origin and no ``copy_with``;
# paramsight has no rule to rebuild it, so it refuses loudly rather than silently
# leaking the unsubstituted alias. (Defined at module scope -- ``type`` statements
# aren't allowed inside a function.)
type _UnsupportedAlias[X] = list[X]


class _UnsupportedBase[Y]:
    y_type = TypeVarValue[Y]()


class _UnsupportedMid[T](_UnsupportedBase[_UnsupportedAlias[T]]): ...


def test_unsupported_type_form_raises_clearly():
    with pytest.raises(TypeError, match="no rule to rebuild"):
        _UnsupportedMid[int].y_type


# ---------------------------------------------------------------------------
# TypeVarValueOption (surface A): unbound + no default yields ``None``, with a
# statically-visible ``type[T] | None`` surface and the canonical idiom.
# ---------------------------------------------------------------------------


class OptBox[T]:
    value_type = TypeVarValueOption[T]()


def test_option_resolves_when_specialized():
    assert OptBox[int].value_type is int
    assert OptBox[str]().value_type is str
    assert_type(OptBox[int].value_type, type[int] | None)


def test_option_yields_none_when_unbound_no_default():
    assert OptBox.value_type is None


class OptDefaultNone[T = None]:
    value_type = TypeVarValueOption[T]()


def test_option_default_none_is_nonetype_not_the_none_sentinel():
    # The coercion is what makes the ``None`` sentinel unambiguous: a default of
    # ``None`` resolves to the truthy ``NoneType`` class, never the singleton, so
    # ``None`` from the descriptor means exactly "unresolved" and nothing else.
    assert OptDefaultNone.value_type is NoneType


class OptChild[U](OptBox[U]): ...


def test_option_inheritance():
    assert OptChild[bool].value_type is bool
    assert OptChild.value_type is None


# ---------------------------------------------------------------------------
# Bound fallback (engine-level, via get_args_at_base) gets the same coercion:
# a forward-ref bound is source-form too and must come back as a ForwardRef.
# ---------------------------------------------------------------------------


class _BoundTarget: ...


class FwdBounded[T: "_BoundTarget"]: ...


def test_bound_fallback_coerces_forward_ref():
    (bound,) = get_args_at_base(FwdBounded, FwdBounded, return_bound_as_fallback=True)
    assert isinstance(bound, ForwardRef)
    assert bound.__forward_arg__ == "_BoundTarget"


# ---------------------------------------------------------------------------
# Nested generic in a base — the resolution edge case, routed through the
# descriptor. The descriptor is on ArchBase; reading it through an Arch alias
# must walk the nested CfgBase[...] specialization.
# ---------------------------------------------------------------------------


class CfgBase[X]: ...


class ArchBase[CfgT]:
    cfg_type = TypeVarValue[CfgT]()


class Arch[T](ArchBase[CfgBase[T]]): ...


class ArchImpl(Arch[int]): ...


def test_nested_generic_in_base_resolves_through_descriptor():
    assert Arch[int].cfg_type == CfgBase[int]
    assert ArchImpl.cfg_type == CfgBase[int]
    assert Arch[str].cfg_type == CfgBase[str]


# ---------------------------------------------------------------------------
# Coexistence with @takes_alias methods
# ---------------------------------------------------------------------------


class WithBoth[T]:
    value_type = TypeVarValue[T]()

    @takes_alias
    @classmethod
    def via_method(cls) -> tuple[type | GenericAlias | None, ...]:
        return get_args_at_base(cls, WithBoth)


def test_takes_alias_method_and_descriptor_coexist():
    assert WithBoth[int].value_type is int
    assert WithBoth[int].via_method() == (int,)


# ---------------------------------------------------------------------------
# Old-style ``Generic[T]`` classes
# ---------------------------------------------------------------------------


_OldT = TypeVar("_OldT")


class OldStyle(Generic[_OldT]):
    value_type = TypeVarValue[_OldT]()


def test_old_style_generic():
    assert OldStyle[int].value_type is int
    assert OldStyle[str].value_type is str


# ---------------------------------------------------------------------------
# Pydantic models (need the descriptor in ignored_types)
# ---------------------------------------------------------------------------


class PydModel[T](BaseModel):
    model_config = ConfigDict(ignored_types=(TypeVarValue,))
    value_type = TypeVarValue[T]()


def test_pydantic_model():
    assert PydModel[int].value_type is int
    assert PydModel[str].value_type is str


class PydChild[U](PydModel[U]): ...


def test_pydantic_inheritance():
    assert PydChild[bool].value_type is bool


# ---------------------------------------------------------------------------
# Misuse — should fail loudly, at class-definition time where possible
# ---------------------------------------------------------------------------


def test_misuse_non_typevar_argument_raises_at_class_def():
    with pytest.raises(TypeError, match="must be a TypeVar"):

        class _Bad:
            x = TypeVarValue[int]()  # type: ignore[valid-type]


def test_misuse_missing_parameterization_raises_at_class_def():
    with pytest.raises(TypeError, match="must be parameterized"):

        class _Bad:
            x = TypeVarValue()  # type: ignore[var-annotated]


def test_misuse_unrelated_typevar_raises_at_class_def():
    _Unrelated = TypeVar("_Unrelated")

    with pytest.raises(TypeError, match="not a typevar of"):

        class _Bad[T]:
            x = TypeVarValue[_Unrelated]()  # type: ignore[valid-type]


# ---------------------------------------------------------------------------
# A worked end-to-end example resembling the original motivation
# ---------------------------------------------------------------------------


class Validated[T]:
    expected_type = TypeVarValue[T]()

    # Note: must be @takes_alias so ``cls`` is the alias (Validated[int]);
    # a plain @classmethod would receive the bare Validated and the descriptor
    # would see no specialization. This is the same constraint @takes_alias
    # methods already have.
    @takes_alias
    @classmethod
    def ensure(cls, val: object) -> T:
        assert isinstance(val, cls.expected_type)
        return val  # type: ignore[return-value]


def test_ensure_roundtrip():
    assert Validated[int].ensure(3) == 3
    assert Validated[str].ensure("x") == "x"
    with pytest.raises(AssertionError):
        Validated[int].ensure("not an int")
    # static surface: this assignment is itself a type assertion
    expected: type[int] = Validated[int].expected_type
    assert expected is int


# ---------------------------------------------------------------------------
# ``__init_subclass__`` must keep firing
#
# Installing the generic-alias proxy patches the owner's ``__init_subclass__``.
# Regression: the patched version used to call ``super(owner, cls)`` and so
# silently ate a user-defined ``__init_subclass__`` on the owner itself.
# ---------------------------------------------------------------------------


def test_user_init_subclass_fires_with_typevar_value():
    seen: list[tuple[str, object]] = []

    class S[T]:
        Type = TypeVarValue[T]()

        def __init_subclass__(cls) -> None:
            # The descriptor must already resolve against the subclass's alias.
            seen.append((cls.__name__, cls.Type))

    class C(S[int]): ...

    assert seen == [("C", int)]


def test_user_init_subclass_fires_with_takes_alias():
    # The user reported the bug "via takes_alias"; that path installs the same
    # proxy, so the owner's own ``__init_subclass__`` must still run.
    seen: list[str] = []

    class S[T]:
        @takes_alias
        @classmethod
        def get_type(cls):
            return get_args_at_base(cls, S)

        def __init_subclass__(cls) -> None:
            seen.append(cls.__name__)

    class C(S[int]): ...

    assert seen == ["C"]


def test_user_init_subclass_super_chain_preserved():
    # When the owner *inherits* (rather than defines) __init_subclass__, the
    # cooperative chain must still reach base hooks via super().
    order: list[str] = []

    class Base:
        def __init_subclass__(cls, **kw) -> None:
            super().__init_subclass__(**kw)
            order.append(f"Base:{cls.__name__}")

    class S[T](Base):
        Type = TypeVarValue[T]()
        # S deliberately defines no __init_subclass__ of its own.

    order.clear()  # drop the entry from S's own creation

    class C(S[int]): ...

    assert order == ["Base:C"]


def test_user_init_subclass_own_hook_calls_super():
    # Owner defines its own hook *and* cooperates with super(); both the user
    # hook and the inherited base hook must run, in the right order.
    order: list[str] = []

    class Base:
        def __init_subclass__(cls, **kw) -> None:
            super().__init_subclass__(**kw)
            order.append(f"Base:{cls.__name__}")

    class S[T](Base):
        Type = TypeVarValue[T]()

        def __init_subclass__(cls, **kw) -> None:
            super().__init_subclass__(**kw)
            order.append(f"S:{cls.__name__}")

    order.clear()

    class C(S[int]): ...

    assert order == ["Base:C", "S:C"]
