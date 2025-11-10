import typing
from collections.abc import Callable
from types import GenericAlias
from typing import (
    Annotated,
    Any,
    TypeGuard,
    TypeIs,
    TypeVar,
    get_args,
    get_origin,
)

from pydantic import BaseModel

type AliasType = GenericAlias | typing._GenericAlias  # type: ignore

_NODEFAULT = typing.NoDefault


def get_args_robust(t: Any) -> tuple[Any, ...]:
    """
    does typing.get_args but handles pydantic generic aliases as well
    """
    if _is_pydantic(t):
        if not hasattr(t, "__pydantic_generic_metadata__"):
            return ()
        return t.__pydantic_generic_metadata__["args"]
    return get_args(t)


def get_origin_robust(ga: Any) -> type | None:
    """
    does typing.get_origin but handles pydantic generic aliases as well
    """
    if _is_pydantic(ga):
        if not hasattr(ga, "__pydantic_generic_metadata__"):
            return None
        res = ga.__pydantic_generic_metadata__["origin"]
        return res
    else:
        res = get_origin(ga)
    assert isinstance(res, type | None)
    return res


def _is_typevar(x: Any) -> TypeGuard[TypeVar]:
    if isinstance(x, TypeVar):
        return True
    return getattr(x, "__class__", type(x)).__name__ == "TypeVar"


def _get_typevar_default(tv: Any) -> Any:
    return getattr(tv, "__default__", getattr(tv, "default", _NODEFAULT))


def unwrap_annotated(param: Any) -> type:
    inner = get_origin_robust(param)
    if inner is Annotated:
        return unwrap_annotated(get_args_robust(param)[0])
    return param


def is_generic_alias(cls: type | GenericAlias) -> TypeGuard[GenericAlias]:
    if _is_pydantic(cls):
        if not hasattr(cls, "__pydantic_generic_metadata__"):
            return False
        return cls.__pydantic_generic_metadata__["origin"] is not None
    if isinstance(cls, typing._GenericAlias):  # type: ignore
        return True
    elif isinstance(cls, GenericAlias):
        return True
    return False


def _make_type_guard[T](t: type[T]) -> Callable[[Any], TypeGuard[T]]:
    def guard(obj: Any) -> TypeGuard[T]:
        return isinstance(obj, t)

    return guard


def _make_typeis_guard[T](t: type[T]) -> Callable[[Any], TypeIs[T]]:
    def guard(obj: Any) -> TypeIs[T]:
        return isinstance(obj, t)

    return guard


def _assert_is_instance[T](obj: Any, cls: type[T]) -> T:
    assert _make_typeis_guard(cls)(obj)
    return obj


def _make_issubclass_guard[T](t: type[T]) -> Callable[[Any], TypeGuard[type[T]]]:
    def guard(obj: Any) -> TypeGuard[type[T]]:
        return issubclass(obj, t)

    return guard


def get_num_typevars(cls: type | GenericAlias) -> int:
    if is_generic_alias(cls):
        cls = _assert_is_instance(get_origin_robust(cls), type)
    return len(cls.__type_params__)


pydantic_model_metaclass = type(BaseModel)


def _is_pydantic(cls):
    return (
        isinstance(cls, BaseModel)
        or isinstance(cls, pydantic_model_metaclass)
        or (
            isinstance(cls, type)
            and (
                issubclass(cls, BaseModel) or issubclass(cls, pydantic_model_metaclass)
            )
        )
    )
