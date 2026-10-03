"""Nested explicit union arguments are type expressions, not base classes."""

from typing import Union

from pydantic import BaseModel

from paramsight.generic_restored_basemodel.generic_basemodel import GenericBaseModel
from paramsight.generic_restored_basemodel.typeref import TypeRef


class First(BaseModel):
    value: int


class Second(BaseModel):
    text: str


class Box[T](BaseModel):
    item: T


class Record[T](GenericBaseModel):
    value: T


def test_nested_union_type_reference_roundtrip() -> None:
    concrete = Box[First | Second]
    ref = TypeRef.from_ga(concrete)
    assert TypeRef.model_validate_json(ref.model_dump_json()).get() is concrete


def test_cold_generic_owner_preserves_nested_union() -> None:
    concrete = Record[Box[First | Second]]
    value = concrete(value=Box[First | Second](item=First(value=7)))
    restored = Record.model_validate_json(value.model_dump_json())
    assert type(restored) is concrete
    assert isinstance(restored.value.item, First)
    assert restored == value


def test_optional_and_typing_union_preserve_the_same_members() -> None:
    concrete = int | None
    ref = TypeRef.from_ga(concrete)
    assert TypeRef.model_validate_json(ref.model_dump_json()).get() == concrete
    assert TypeRef.from_ga(Union[int, str]).get() == int | str  # noqa: UP007 - legacy spelling under test
