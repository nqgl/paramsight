from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, GetCoreSchemaHandler, computed_field
from pydantic_core import PydanticCustomError, core_schema

from paramsight import get_resolved_typevars_for_base, takes_alias
from paramsight.generic_restored_basemodel.typeref import TypeRef
from paramsight.type_utils import get_num_typevars, get_origin_robust, is_generic_alias

# keyname = "_gbm_type_parameter_info"


# class GenericModel(BaseModel):
#     @computed_field
#     @property
#     def _gbm_type_parameter_info(self) -> tuple[TypeRef, ...]:
#         return tuple(TypeRef.from_ga(t) for t in self._gbm_get_generic_type())

#     assert _gbm_type_parameter_info.__name__ == keyname

#     @classmethod
#     def _gbm_get_generic_type(cls):
#         return get_resolved_typevars_for_base(cls, get_origin_robust(cls) or cls)

#     @classmethod
#     def model_validate_json(
#         cls,
#         json_data: str | bytes | bytearray,
#         *,
#         strict: bool | None = None,
#         extra: None | Literal["allow"] | Literal["ignore"] | Literal["forbid"] = None,
#         context: Any | None = None,
#         by_alias: bool | None = None,
#         by_name: bool | None = None,
#     ) -> Self:
#         pass

#     @classmethod
#     def _gbm_specialize_from_object(cls, obj: Any):
#         tvs = tuple(TypeRef.model_validate(tv).get() for tv in obj[keyname])
#         if len(tvs) == 0:
#             return cls
#         return cls.__class_getitem__(tvs)

#     @classmethod
#     def model_validate(
#         cls,
#         obj: Any,
#         *,
#         strict: bool | None = None,
#         extra: None | Literal["allow"] | Literal["ignore"] | Literal["forbid"] = None,
#         from_attributes: bool | None = None,
#         context: Any | None = None,
#         by_alias: bool | None = None,
#         by_name: bool | None = None,
#     ) -> Self:
#         if is_generic_alias(cls):
#             return super().model_validate(
#                 obj,
#                 strict=strict,
#                 extra=extra,
#                 from_attributes=from_attributes,
#                 context=context,
#                 by_alias=by_alias,
#                 by_name=by_name,
#             )
#         tvs = tuple(TypeRef.model_validate(tv).get() for tv in obj["generic_type"])
#         if len(tvs) == 0:
#             return super().model_validate(
#                 obj,
#                 strict=strict,
#                 extra=extra,
#                 from_attributes=from_attributes,
#                 context=context,
#                 by_alias=by_alias,
#                 by_name=by_name,
#             )
#         return cls.__class_getitem__(tvs).model_validate(
#             obj,
#             strict=strict,
#             extra=extra,
#             from_attributes=from_attributes,
#             context=context,
#             by_alias=by_alias,
#             by_name=by_name,
#         )


class GenericBaseModel(BaseModel):
    _GENERIC_KEY: ClassVar[str] = "generic_type"

    @computed_field
    @property
    def generic_type(self) -> tuple[TypeRef, ...]:
        return tuple(TypeRef.from_ga(t) for t in self.get_generic_type())

    @takes_alias
    @classmethod
    def get_generic_type(cls):
        return get_resolved_typevars_for_base(cls, get_origin_robust(cls) or cls)

    @classmethod
    def _select_specialized_alias(
        cls,
        value: Any,
    ) -> tuple[type["GenericBaseModel"], Any]:
        if get_num_typevars(cls) == 0 or is_generic_alias(cls):
            return cls, value
        if isinstance(value, GenericBaseModel):
            return type(value), value

        if not isinstance(value, Mapping):
            raise PydanticCustomError(
                "generic_model_type",
                "GenericModel expects a mapping when deserializing unspecialized type",
            )
        try:
            raw_tvs = value[cls._GENERIC_KEY]
        except KeyError:
            # No generic_type key: either treat as "unspecialized" or error
            # raise PydanticCustomError(
            #     "generic_model_type",
            #     "GenericModel expects a mapping with a generic_type key"
            #     " when deserializing unspecialized type",
            # ) from e
            return cls, value

        tvs = tuple(TypeRef.model_validate(tv).get() for tv in raw_tvs)
        if not tvs:
            return cls, value

        alias = cls.__class_getitem__(tvs)
        return alias, value

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source: type[Any],
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        """
        Customize validation so that the *unspecialized* GenericModel
        dynamically dispatches to GenericModel[...].

        Specialized aliases (GenericModel[int], etc.) keep the default schema.
        """
        # If this is already a specialized alias like GenericModel[int],
        # just generate the normal schema.
        if is_generic_alias(cls):
            return handler(source)

        # For the bare GenericModel, we wrap the default schema with a dispatcher.
        inner_schema = handler(source)

        def dispatch(
            value: Any,
            validator: core_schema.ValidatorFunctionWrapHandler,
        ) -> Any:
            alias, normalized = cls._select_specialized_alias(value)
            if alias is cls:
                # Use the "base" schema for GenericModel itself.
                # `validator` validates using `inner_schema`.
                return validator(normalized)

            # Delegate to the specialized alias's validator.
            # This preserves all strict/extra/from_attributes/context handling.
            return alias.__pydantic_validator__.validate_python(normalized)

        return core_schema.no_info_wrap_validator_function(
            dispatch,
            inner_schema,
        )


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
