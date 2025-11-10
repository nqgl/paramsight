# tests/test_typevar_resolution_hypothesis.py
from __future__ import annotations

from typing import Any, Protocol
import typing

import pytest
from hypothesis import given, settings, strategies as st

import torch.nn as nn
from attrs import define
from pydantic import BaseModel

from nicetv.aliasclassmethod import takes_alias
from nicetv.paramsight import get_resolved_typevars_for_base

# ---------------------------------------------------------------------------
# Hypothesis configuration
# ---------------------------------------------------------------------------

TYPE_STRAT = st.sampled_from([int, float, str, bytes, bool])
TYPE_PAIR_STRAT = st.tuples(TYPE_STRAT, TYPE_STRAT)

DEFAULT_SETTINGS = settings(max_examples=12, deadline=None)

# ---------------------------------------------------------------------------
# Classes under test (from your snippet, lightly trimmed of prints/side effects)
# ---------------------------------------------------------------------------


# helper near the top of the file, after TYPE_STRAT is defined
def _example_for_type(t: type):
    return {
        int: 1,
        float: 1.0,
        str: "x",
        bytes: b"x",
        bool: True,
    }[t]


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_instance_paths_match_classmethod_behavior(t: type):
    # BaseModel-only
    assert CheckBaseModel[t]().check() == (t,)

    # BaseModel + CheckTVCls mixin — pass a value compatible with T
    val = _example_for_type(t)
    assert CheckBaseModel2[t](field=val).check() == (t,)

    # attrs + CheckTVCls
    assert CheckAttrs[t]().check() == (t,)

    # nn.Module + CheckTVCls (has no required __init__ args)
    assert CheckTorch[t]().check() == (t,)


class Variations:
    has_init_subclass: bool = True


class ClassMethod(Protocol):
    @classmethod
    def __call__(cls: type[Any], *args: Any, **kwargs: Any) -> Any: ...


class CheckTVCls[T]:
    @takes_alias
    @classmethod
    def check(cls):
        return get_resolved_typevars_for_base(cls, CheckTVCls)


class CheckPlain[T](CheckTVCls[T]): ...


class CheckPlainSuper[T](CheckTVCls[T]):
    @takes_alias
    @classmethod
    def check(cls):
        return super().check()


class CheckPlainSuperDuper_2[T1, T2](CheckPlainSuper[T2]):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        return super().check()


class CheckPlainSuperDuper_1[T1, T2](CheckPlainSuper[T1]):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        return super().check()


class CheckPlain2[T1, T2](CheckPlain[T2]): ...


class CheckBasicBaseModel[T](BaseModel):
    @takes_alias
    @classmethod
    def check(cls):
        # Just validate that the “cls” being passed is an alias (not a bare type)
        assert type(cls) is not type


class NonGenericBaseModel(BaseModel):
    t: int = 2

    @takes_alias
    @classmethod
    def check(cls):
        return get_resolved_typevars_for_base(cls, NonGenericBaseModel)


class CheckBaseModel[T](BaseModel):
    @takes_alias
    @classmethod
    def check(cls):
        return get_resolved_typevars_for_base(cls, CheckBaseModel)


class CheckBaseModel2[T](BaseModel, CheckTVCls[T]):
    field: T


@define
class CheckAttrs[T](CheckTVCls[T]): ...


class CheckTorch[T](nn.Module, CheckTVCls[T]): ...


# ---------------------------------------------------------------------------
# Tests (property-based where it helps; direct asserts for “NoDefault” cases)
# ---------------------------------------------------------------------------


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_plain_specialized_returns_tuple_of_the_type(t: type):
    assert CheckPlain[t].check() == (t,)


@DEFAULT_SETTINGS
@given(t1=TYPE_STRAT, t2=TYPE_STRAT)
def test_plain2_maps_to_second_type_param(t1: type, t2: type):
    assert CheckPlain2[t1, t2].check() == (t2,)


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_super_keeps_alias_through_single_override(t: type):
    assert CheckPlainSuper[t].check() == (t,)


@DEFAULT_SETTINGS
@given(t1=TYPE_STRAT, t2=TYPE_STRAT)
def test_super_duper_variants_route_correct_typevar(t1: type, t2: type):
    # Variant that pulls T1
    assert CheckPlainSuperDuper_1[t1, t2].check() == (t1,)
    # Variant that pulls T2
    assert CheckPlainSuperDuper_2[t1, t2].check() == (t2,)


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_basemodel_and_mixins_classmethod_paths(t: type):
    assert CheckBaseModel[t].check() == (t,)
    assert CheckBaseModel2[t].check() == (t,)
    assert CheckAttrs[t].check() == (t,)
    assert CheckTorch[t].check() == (t,)


@DEFAULT_SETTINGS
@given(t=TYPE_STRAT)
def test_checkbasic_basemodel_validates_alias_cls(t: type):
    # Should not raise due to internal assertion that cls is not a bare type
    CheckBasicBaseModel[t].check()


def test_unspecialized_returns_nodefault_tuples_for_all_relevant_classes():
    # These should all resolve to (typing.NoDefault,) when unspecialized
    candidates = [
        CheckPlain2,
        CheckBaseModel,
        CheckBaseModel2,
        CheckPlain,
        CheckPlainSuper,
        CheckPlainSuperDuper_1,
        CheckPlainSuperDuper_2,
        CheckAttrs,
        CheckTorch,
    ]
    for cls in candidates:
        assert cls.check() == (typing.NoDefault,)

    # Instance of an unspecialized BaseModel+Mixin should also yield NoDefault
    assert CheckBaseModel2(field=1).check() == (typing.NoDefault,)


def test_non_generic_basemodel_check_is_stable_unspecialized():
    # Not in your original asserts, but ensures non-generic BaseModel helper is sane.
    # For a non-generic base, expect no type vars; keep the expectation flexible:
    out = NonGenericBaseModel.check()
    assert isinstance(out, tuple)
    # Either empty tuple or NoDefault depending on your resolver's semantics
    assert out in {(), (typing.NoDefault,)}
