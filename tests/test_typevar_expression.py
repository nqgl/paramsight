"""Tests for the experimental ``TypeVarExpression`` / ``TypeVarExpressionOption``
descriptors -- resolving a whole type expression over a class's typevars.
"""

from typing import Self, TypeVar, assert_type, get_args, get_origin

import pytest

from paramsight import TypeVarExpression, TypeVarExpressionOption

# ---------------------------------------------------------------------------
# Basic expressions over a single typevar
# ---------------------------------------------------------------------------


class Box[T]:
    pair = TypeVarExpression[tuple[T, T]]()
    maybe = TypeVarExpression[T | None]()
    listish = TypeVarExpression[list[T] | list[int]]()
    opt = TypeVarExpressionOption[tuple[T, T]]()


def test_expression_resolves_typevars():
    assert Box[int].pair == tuple[int, int]
    assert Box[str].maybe == str | None
    assert Box[str].listish == list[str] | list[int]


def test_expression_static_type():
    # pyright substitutes the receiver's typevars into the expression and
    # evaluates ``type[X]`` itself.
    assert_type(Box[int].pair, type[tuple[int, int]])


def test_expression_instance_access():
    assert Box[int]().pair == tuple[int, int]


# ---------------------------------------------------------------------------
# Self -> the receiver (kept as the alias it was reached through)
# ---------------------------------------------------------------------------


class Holder[T]:
    me = TypeVarExpression[T | Self]()


def test_self_resolves_to_receiver_alias():
    members = get_args(Holder[int].me)
    assert members[0] is int
    # ``Self`` -> ``Holder[int]`` (a paramsight alias proxy carrying the args).
    assert get_origin(members[1]) is Holder
    assert get_args(members[1]) == (int,)


class HolderSub[U](Holder[U]): ...


def test_self_is_the_most_derived_receiver():
    members = get_args(HolderSub[str].me)
    assert get_origin(members[1]) is HolderSub
    assert get_args(members[1]) == (str,)


# ---------------------------------------------------------------------------
# Multiple typevars and variadic typevars in one expression
# ---------------------------------------------------------------------------


class Multi[A, B, *Ts]:
    combo = TypeVarExpression[dict[A, B] | tuple[*Ts]]()


def test_multiple_and_variadic_typevars_in_expression():
    assert Multi[int, str, bool, bytes].combo == (dict[int, str] | tuple[bool, bytes])


# ---------------------------------------------------------------------------
# Unresolved: raise (Expression) vs None (ExpressionOption)
# ---------------------------------------------------------------------------


def test_unresolved_when_bare_raises_or_none():
    with pytest.raises(LookupError, match="did not resolve to a concrete type"):
        Box.pair
    assert Box.opt is None


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


class EBase[X]:
    e = TypeVarExpression[list[X] | None]()


class EChild[Y](EBase[Y]): ...


def test_expression_through_inheritance():
    assert EChild[int].e == list[int] | None


# ---------------------------------------------------------------------------
# Validation: only the owning class's own typevars are allowed
# ---------------------------------------------------------------------------


def test_foreign_typevar_raises_at_class_definition():
    _Foreign = TypeVar("_Foreign")
    with pytest.raises(TypeError, match="may reference only"):

        class _Bad[T]:
            x = TypeVarExpression[T | _Foreign]()  # type: ignore[valid-type]


def test_missing_parameterization_raises_at_class_definition():
    with pytest.raises(TypeError, match="must be parameterized"):

        class _Bad:
            x = TypeVarExpression()  # type: ignore[var-annotated]
