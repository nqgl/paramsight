"""Bound generic validation projects subclasses without discarding inner types."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from paramsight.generic_restored_basemodel.generic_basemodel import (
    GenericBaseModel,
    _origin_issubclass,
)


class Result:
    pass


class OtherResult:
    pass


class Producer[OutT](BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str


class Transform[InT](Producer[Result]):
    input: InT


class OtherTransform[InT](Producer[OtherResult]):
    input: InT


class Request[SourceT: Producer[Result]](GenericBaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    source: SourceT
    destination: Path


def test_generic_subclass_fixed_base_output_survives_bare_owner_json() -> None:
    source = Transform[str](name="concrete", input="preserve")
    request = Request[Transform[str]](source=source, destination=Path("/tmp/example"))
    restored = Request.model_validate_json(request.model_dump_json())
    assert type(restored) is type(request)
    assert type(restored.source) is type(source)
    assert restored == request


def test_wrong_projected_output_is_rejected() -> None:
    assert not _origin_issubclass(OtherTransform[str], Producer[Result])
    request = Request[Transform[str]](
        source=Transform[str](name="concrete", input="preserve"),
        destination=Path("/tmp/example"),
    )
    invalid = request.model_dump()
    from paramsight.generic_restored_basemodel.typeref import TypeRef

    invalid["generic_type"] = [TypeRef.from_ga(OtherTransform[str]).model_dump()]
    with pytest.raises(ValidationError, match="subclasses"):
        Request.model_validate(invalid)


@pytest.mark.parametrize(
    ("value", "bound", "valid"),
    [
        (list[str], list[str], True),
        (list[str], list[int], False),
        (Transform[str], Producer[Result], True),
        (OtherTransform[str], Producer[Result], False),
    ],
)
def test_parameterized_guards_remain_invariant(value, bound, valid) -> None:
    assert _origin_issubclass(value, bound) is valid
