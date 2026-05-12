"""Tests for the ``TypeVarValue`` descriptor.

Covers runtime resolution and (via ``typing.assert_type``, which is a runtime
no-op but checked by static type checkers) the narrowed ``type[T]`` surface.
"""

from types import GenericAlias
from typing import Generic, TypeVar, assert_type

import pytest
from pydantic import BaseModel, ConfigDict

from paramsight import TypeVarValue, get_args_at_base, takes_alias

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
