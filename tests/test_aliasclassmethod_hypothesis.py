# tests/test_aliasclassmethod_hypothesis.py
from typing import Any, Protocol

import pytest
import torch.nn as nn
from attrs import define
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from paramsight.aliasclassmethod import (
    _is_specialized_generic,
    takes_alias,
)

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

# Keep the tested type universe modest and deterministic for speed.
TYPE_STRAT = st.sampled_from([int, float, str, bytes, bool])

# Hypothesis can exercise pairs (for CheckPlain2).
TYPE_PAIR_STRAT = st.tuples(TYPE_STRAT, TYPE_STRAT)

# Slightly reduce run time but keep good coverage.
DEFAULT_SETTINGS = settings(max_examples=12, deadline=None)


# ---------------------------------------------------------------------------
# Original “under test” classes (kept as in the provided snippet)
# ---------------------------------------------------------------------------


class Variations:
    has_init_subclass: bool = True


class ClassMethod(Protocol):
    @classmethod
    def __call__(cls: type[Any], *args: Any, **kwargs: Any) -> Any: ...


class CheckCls:
    @takes_alias
    @classmethod
    def check(cls):
        # Must see a specialized generic alias when called via C[T].check()
        assert _is_specialized_generic(cls)
        cls.check_2(2)

    @takes_alias
    @classmethod
    def check_2(cls, arg: int):
        """checking that chained aliasclassmethod decorators work"""
        assert _is_specialized_generic(cls)

    @takes_alias
    @classmethod
    def check_non_generic(cls):
        """check that non-generic passes correctly"""
        # For plain (non-specialized) classes, cls should be a real class type
        assert type(cls) in (type, type(BaseModel))

    @classmethod
    def normal_classmethod(cls):
        # Should behave like a normal classmethod (no alias passing)
        return None

    @classmethod
    def normal_classmethod2(cls, arg: int):
        return None

    if Variations.has_init_subclass:

        def __init_subclass__(cls) -> None:
            super().__init_subclass__()


class CheckPlain[T](CheckCls): ...


# No side effects like checkme() calls here


class CheckPlainSuper[T](CheckCls):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        # Chained super() should still resolve to the aliased parent method
        return super().check()


class CheckPlainSuperDuper[T1](CheckPlainSuper[T1]):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        # One more hop of super()
        return super().check()


class CheckPlain2[T1, T2](CheckPlain[T2]): ...


class CheckBasicBaseModel[T](BaseModel):
    @takes_alias
    @classmethod
    def check(cls):
        # aliasclassmethod should supply an alias-like “cls”
        assert _is_specialized_generic(cls)

    @takes_alias
    @classmethod
    def check_non_generic(cls):
        assert not _is_specialized_generic(cls)
        assert type(cls) in (type, type(BaseModel))


class CheckBaseModel[T](CheckCls, BaseModel): ...


class CheckBaseModel2[T](BaseModel, CheckCls):
    field: T


@define
class CheckAttrs[T](CheckCls): ...


class CheckTorch[T](nn.Module, CheckCls): ...


class DoesNothing: ...


class BaseModelWithNothing[T](BaseModel, DoesNothing, CheckCls): ...


class Simple[T](CheckCls): ...


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_check_plain_specialized_generic_classmethod(t: type):
    # C[T].check() should see a specialized alias in cls (internal assert verifies)
    CheckPlain[t].check()


@DEFAULT_SETTINGS
@given(t1=TYPE_STRAT, t2=TYPE_STRAT)
def test_check_plain2_inherits_and_still_aliases(t1: type, t2: type):
    # Inherits generic param positionally and keeps alias passing
    CheckPlain2[t1, t2].check()


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_super_works_across_alias_classmethods(t: type):
    # super() chains should not lose the alias across overrides
    CheckPlainSuper[t].check()
    CheckPlainSuperDuper[t].check()


def test_normal_classmethod_is_normal():
    # Sanity: normal classmethod does not engage alias plumbing
    assert CheckPlain[int].normal_classmethod() is None
    assert CheckPlain.normal_classmethod() is None


def test__is_specialized_generic_truths():
    # Positive: specialized generics
    assert _is_specialized_generic(CheckPlain[int])
    assert _is_specialized_generic(CheckPlain2[int, str])
    # Negative: unspecialized generics / plain classes
    assert not _is_specialized_generic(CheckPlain)
    assert not _is_specialized_generic(CheckPlain2)
    assert not _is_specialized_generic(Simple)


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_aliasclassmethod_on_pydantic_base_model(t: type):
    # The aliasclassmethod variant should supply a non-type cls
    CheckBasicBaseModel[t].check()


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_mixtures_with_basemodel_and_attrs_and_torch(t: type):
    # Classmethod paths
    CheckBaseModel[t].check()
    CheckBaseModel2[t].check()
    CheckAttrs[t].check()
    CheckTorch[t].check()

    # Instance paths (where your CheckCls methods are also invoked)
    CheckBaseModel[t]().check()
    value = 1
    try:
        value = t(value)
    except Exception:
        pass
    if isinstance(value, t):
        CheckBaseModel2[t](field=value).check()
    CheckAttrs[t]().check()
    # nn.Module subclass with no parameters should instantiate fine
    CheckTorch[t]().check()


@pytest.mark.parametrize(
    "cls",
    [
        CheckPlain,
        CheckPlainSuper,
        CheckPlainSuperDuper,
        CheckBaseModel,
        CheckBaseModel2,
        CheckAttrs,
        CheckTorch,
        CheckBasicBaseModel,  # still okay in non-generic form
        BaseModelWithNothing,
        Simple,
    ],
)
def test_check_non_generic_accepts_plain_classes(cls):
    # Your implementation asserts that for non-specialized calls
    # the “cls” seen by the method is a regular class type.
    cls.check_non_generic()
