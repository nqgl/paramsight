from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import (
    BaseModel,
    ValidationInfo,
    computed_field,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError
from pydantic_core.core_schema import SerializerFunctionWrapHandler

from paramsight import get_args_at_base, takes_alias
from paramsight.generic_restored_basemodel.typeref import TypeRef
from paramsight.type_utils import get_num_typevars, get_origin_robust, is_generic_alias


def _origin_issubclass(value: Any, base: Any) -> bool:
    """``issubclass`` that tolerates generic aliases without raising.

    Both operands are reduced to their unsubscripted origin (so ``list[int]``
    is compared as ``list``). This is a *validation* check on possibly-hostile
    deserialized type info, so it fails **closed**: if a meaningful class
    comparison can't be made, return ``False`` and let the caller reject it
    rather than waving it through.
    """
    value = get_origin_robust(value) or value
    base = get_origin_robust(base) or base
    if isinstance(value, type) and isinstance(base, type):
        return issubclass(value, base)
    return False


class GenericBaseModel(BaseModel):
    _GENERIC_KEY: ClassVar[str] = "generic_type"

    @computed_field
    @property
    def generic_type(self) -> tuple[TypeRef, ...]:
        return tuple(TypeRef.from_ga(t) for t in self.get_type_parameters())

    @model_serializer(mode="wrap")
    def _drop_empty_generic_type(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        data = handler(self)
        if isinstance(data, dict) and not data.get(self._GENERIC_KEY):
            data.pop(self._GENERIC_KEY, None)
        return data

    @takes_alias
    @classmethod
    def get_type_parameters(cls):
        return get_args_at_base(cls, get_origin_robust(cls) or cls)

    @takes_alias
    @classmethod
    def _select_specialized_alias(
        cls,
        value: Any,
    ) -> tuple[Any, Any]:
        """Pick the model to validate ``value`` against.

        Returns ``(target, value)``. ``target`` is a ``GenericBaseModel``
        (sub)class or a parameterized alias of one — typed ``Any`` because it
        spans pydantic-generated specialization classes and typing aliases,
        which have no common static type. If ``target is cls`` the caller
        validates normally; otherwise it delegates to
        ``target.model_validate``.
        """
        if get_num_typevars(cls) == 0:
            return cls, value
        ga_params = None
        if is_generic_alias(cls):
            ga_params = cls.get_type_parameters()
        if isinstance(value, GenericBaseModel):
            return type(value), value

        if not isinstance(value, Mapping):
            if ga_params is not None:
                return cls, value
            raise PydanticCustomError(
                "generic_model_type",
                "GenericModel expects a mapping when deserializing unspecialized type",
            )
        try:
            raw_tvs = value[cls._GENERIC_KEY]
        except KeyError:
            return cls, value

        tvs = tuple(TypeRef.model_validate(tv).get() for tv in raw_tvs)
        if not tvs:
            return cls, value
        if ga_params is not None:
            if len(tvs) != len(ga_params):
                raise PydanticCustomError(
                    "generic_model_type",
                    "GenericModel expects {expected} type arguments, got {actual}",
                    {"expected": len(ga_params), "actual": len(tvs)},
                )
            if not all(  # assuming covariance? not clear if this is best
                _origin_issubclass(tv, param)
                for tv, param in zip(tvs, ga_params, strict=True)
            ):
                raise PydanticCustomError(
                    "generic_model_type",
                    "GenericModel expects type arguments to be subclasses "
                    "of {expected}",
                    {"expected": ga_params},
                )

        # tvs are runtime-resolved types/aliases; the checker can't model
        # dynamic pydantic specialization here.
        alias = cls.__class_getitem__(tvs)  # pyright: ignore[reportArgumentType]
        return alias, value

    @model_validator(mode="wrap")
    @classmethod
    def _dispatch_to_specialized_alias(
        cls, value: Any, handler: Any, info: ValidationInfo
    ) -> Any:
        """Make the *unspecialized* GenericBaseModel dispatch to the right
        ``GenericBaseModel[...]`` based on the carried ``generic_type`` info.

        Specialized aliases and non-generic subclasses validate normally — only
        the bare generic model needs to dispatch.
        """
        if is_generic_alias(cls) or get_num_typevars(cls) == 0:
            return handler(value)
        alias, normalized = cls._select_specialized_alias(value)
        if alias is cls:
            return handler(normalized)
        return alias.model_validate(normalized, context=info.context)


class C1(GenericBaseModel):
    y: str = "a"
    z: int = 1


class C2(GenericBaseModel):
    x: int = 5
    z: str = "def"


class GbmTest[T1, T2](GenericBaseModel):
    x1: T1
    x2: T2


class GbmTest2[T, Ta = int, Tb = str](GenericBaseModel):
    x1: GbmTest[Ta, T]
    x2: GbmTest[Tb, T]
