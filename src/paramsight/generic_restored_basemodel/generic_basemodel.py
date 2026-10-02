from collections.abc import Mapping
from functools import lru_cache
from types import UnionType
from typing import Any, ClassVar, TypeVar

from pydantic import (
    BaseModel,
    SerializationInfo,
    ValidationInfo,
    computed_field,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError, SchemaSerializer, to_json
from pydantic_core.core_schema import SerializerFunctionWrapHandler

from paramsight import get_args_at_base, takes_alias
from paramsight.generic_restored_basemodel.typeref import TypeRef
from paramsight.type_utils import (
    get_args_robust,
    get_num_typevars,
    get_origin_robust,
    get_parameters,
    is_generic_alias,
)


def _origin_issubclass(value: Any, base: Any) -> bool:
    """``issubclass`` that tolerates generic aliases without raising.

    Ordinary class arguments may narrow covariantly, including under a bounded
    TypeVar. Parameterized arguments must retain their inner arguments: treating
    list[str] as list[int] merely because both originate from list is unsafe.
    This is conservative validation, not a general-purpose subtype solver.
    """
    if value == base or base is Any:
        return True
    if isinstance(base, TypeVar):
        if base.__constraints__:
            return any(_origin_issubclass(value, item) for item in base.__constraints__)
        return base.__bound__ is None or _origin_issubclass(value, base.__bound__)
    if get_origin_robust(value) is UnionType:
        return all(_origin_issubclass(item, base) for item in get_args_robust(value))
    if get_origin_robust(base) is UnionType:
        return any(_origin_issubclass(value, item) for item in get_args_robust(base))
    value_origin = get_origin_robust(value) or value
    base_origin = get_origin_robust(base) or base
    if isinstance(value_origin, type) and isinstance(base_origin, type):
        if not issubclass(value_origin, base_origin):
            return False
        base_args = get_args_robust(base)
        if base_args:
            # A subclass can introduce unrelated parameters while fixing the
            # bound base's output: Child[Input] may inherit Producer[Output].
            # Compare at the declared base, not at the subclass's own arity.
            try:
                projected = get_args_at_base(value, base_origin)
            except (TypeError, ValueError, KeyError):
                return False
            if projected != base_args:
                return False
        return True
    return False


@lru_cache(maxsize=128)
def _concrete_serializer(
    model: type[BaseModel], schema_identity: int
) -> SchemaSerializer:
    """Serialize this model's schema, without recursively invoking its wrapper.

    Only the root model's serializer is removed. Nested field schemas and their
    serializers remain intact. The schema identity invalidates this bounded
    cache after model_rebuild(), without mutating Pydantic's shared schema.
    """

    def unwrap(schema: Any) -> Any:
        if not isinstance(schema, dict):
            return schema
        if schema.get("type") == "model":
            if schema.get("cls") is not model:
                return schema
            result = schema.copy()
            result.pop("serialization", None)
            return result
        result = schema.copy()
        if "schema" in schema:
            result["schema"] = unwrap(schema["schema"])
        if "definitions" in schema:
            result["definitions"] = [unwrap(item) for item in schema["definitions"]]
        return result

    return SchemaSerializer(unwrap(model.__pydantic_core_schema__))


class GenericBaseModel(BaseModel):
    _GENERIC_KEY: ClassVar[str] = "generic_type"

    @computed_field(alias="generic_type")
    @property
    def generic_type(self) -> tuple[TypeRef, ...]:
        return tuple(TypeRef.from_ga(t) for t in self.get_type_parameters())

    @model_serializer(mode="wrap")
    def _drop_empty_generic_type(
        self, _handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        # A field annotated G[Base] may contain G[Derived]. Serialize using the
        # concrete model's schema so its carried type and fields stay consistent.
        # Unlike serialize_as_any=True, this does not broaden ordinary declared
        # fields or disclose unrelated subclass-only fields.
        serializer = _concrete_serializer(
            type(self), id(type(self).__pydantic_core_schema__)
        )
        # Pydantic's SerializationInfo and serializer signatures disagree on
        # mixed-key include/exclude masks. Forward its own masks intact.
        selection: dict[str, Any] = {"include": info.include, "exclude": info.exclude}
        data = serializer.to_python(
            self,
            **selection,
            mode=info.mode,
            by_alias=info.by_alias,
            exclude_unset=info.exclude_unset,
            exclude_defaults=info.exclude_defaults,
            exclude_none=info.exclude_none,
            exclude_computed_fields=info.exclude_computed_fields,
            round_trip=info.round_trip,
            context=info.context,
            serialize_as_any=info.serialize_as_any,
        )
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
        cls: Any,
        value: Any,
    ) -> tuple[Any, Any]:
        """Pick the model to validate ``value`` against.

        Returns ``(target, value)``. ``target`` is a ``GenericBaseModel``
        (sub)class or a parameterized alias of one. The receiver and target use
        ``Any`` at this reflection boundary because the runtime generic arity
        is not expressible as ``type[Self]`` and the supported receiver types
        span pydantic-generated specialization classes and typing aliases,
        which have no common static type. If ``target is cls`` the caller
        validates normally; otherwise it delegates to
        ``target.model_validate``.
        """
        if get_num_typevars(cls) == 0:
            return cls, value
        origin: Any = get_origin_robust(cls) or cls
        ga_params = (
            cls.get_type_parameters() if is_generic_alias(cls) else get_parameters(cls)
        )
        if isinstance(value, GenericBaseModel):
            if not _origin_issubclass(type(value), origin):
                return cls, value
            tvs = get_args_at_base(type(value), origin)
            normalized = value
            alias = type(value)
        else:
            if not isinstance(value, Mapping):
                return cls, value
            try:
                raw_tvs = value[cls._GENERIC_KEY]
            except KeyError:
                return cls, value

            tvs = tuple(TypeRef.model_validate(tv).get() for tv in raw_tvs)
            # This is transport metadata, not a user field. Keep extra='forbid'
            # meaningful for other keys, without mutating the caller's mapping.
            normalized = dict(value)
            del normalized[cls._GENERIC_KEY]
            alias = None
        if len(tvs) != len(ga_params):
            raise PydanticCustomError(
                "generic_model_type",
                "GenericModel expects {expected} type arguments, got {actual}",
                {"expected": len(ga_params), "actual": len(tvs)},
            )
        if not all(
            _origin_issubclass(tv, param)
            for tv, param in zip(tvs, ga_params, strict=True)
        ):
            raise PydanticCustomError(
                "generic_model_type",
                "GenericModel expects type arguments to be subclasses of {expected}",
                {"expected": ga_params},
            )
        return (origin[*tvs] if alias is None else alias), normalized

    @model_validator(mode="wrap")
    @classmethod
    def _dispatch_to_specialized_alias(
        cls, value: Any, handler: Any, info: ValidationInfo
    ) -> Any:
        """Make the *unspecialized* GenericBaseModel dispatch to the right
        ``GenericBaseModel[...]`` based on the carried ``generic_type`` info.

        Compatible concrete specializations are preserved under more general
        annotations. Incompatible carried arguments are rejected before dispatch.

        Dispatch preserves JSON mode and context, but Pydantic does not
        expose per-call validation overrides through ValidationInfo. Such
        overrides are not forwarded to the selected model by this boundary.
        """
        if get_num_typevars(cls) == 0:
            return handler(value)
        alias, normalized = cls._select_specialized_alias(value)
        if alias is cls:
            return handler(normalized)
        if info.mode == "json":
            # Re-enter JSON validation so strict JSON-aware fields such as Path
            # do not receive Python-mode strings. Context is carried explicitly.
            return alias.model_validate_json(to_json(normalized), context=info.context)
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
