import types
import typing
from collections.abc import Callable
from types import GenericAlias
from typing import (
    Annotated,
    Any,
    TypeGuard,
    TypeIs,
    TypeVar,
    cast,
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
    if res is typing.Union:
        # `types.UnionType` is the runtime origin of `X | Y`; it's a class,
        # but pyright won't narrow type[UnionType] to `type` (it's @final).
        return cast(type, types.UnionType)
    assert isinstance(res, type | None)
    return res


# These ``isinstance`` checks assume the stdlib typevar classes are canonical.
# That holds on 3.13 (where ``typing_extensions`` re-exports them), but NOT on
# earlier interpreters, where ``typing_extensions`` ships distinct backport
# classes a bare ``isinstance(x, typing.TypeVar)`` would miss. See
# docs/python-version-support.md before lowering the floor.
def _is_typevar(x: Any) -> TypeGuard[TypeVar]:
    return isinstance(x, TypeVar)


def _is_typevartuple(x: Any) -> bool:
    """``*Ts`` -- a PEP 646 ``TypeVarTuple``. Not a ``TypeVar``; binds to a
    *sequence* of types and appears in containers wrapped in ``Unpack[...]``."""
    return isinstance(x, typing.TypeVarTuple)


def _is_paramspec(x: Any) -> bool:
    """``**P`` -- a PEP 612 ``ParamSpec``. Not a ``TypeVar``; binds to a parameter
    list and appears as the first argument of a ``Callable``."""
    return isinstance(x, typing.ParamSpec)


def _is_unpack(x: Any) -> bool:
    """``*Ts`` / ``Unpack[Ts]`` as it appears inside a container's arguments."""
    return get_origin(x) is typing.Unpack


def _unpack_inner(x: Any) -> Any:
    """The ``TypeVarTuple`` (or other unpackable) inside an ``Unpack[...]``."""
    (inner,) = get_args(x)
    return inner


def _get_typevar_default(tv: Any) -> Any:
    return getattr(tv, "__default__", getattr(tv, "default", _NODEFAULT))


def coerce_to_type_form(value: Any) -> Any:
    """Coerce a raw value into the type-domain form that subscription produces.

    A typevar's ``__default__`` is stored in *source* form: ``T = None`` keeps
    the ``None`` singleton and ``T = "Foo"`` keeps the bare string ``"Foo"``.
    Type *subscription* normalizes those -- ``C[None]`` yields ``NoneType`` and
    ``C["Foo"]`` yields ``ForwardRef('Foo')`` -- via ``typing._type_convert``.
    Running a default through the same conversion keeps the default branch
    (``C``) and the explicit branch (``C[default]``) byte-for-byte identical;
    notably this is *conversion*, not evaluation, so (like subscription) it
    leaves nested strings alone and never needs a namespace.

    Falls back to a manual ``None -> NoneType`` if the private ``typing`` helper
    ever disappears -- that being the one coercion paramsight actually relies on.
    """
    convert = getattr(typing, "_type_convert", None)
    if convert is not None:
        try:
            return convert(value)
        except Exception:
            pass
    return type(None) if value is None else value


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


def get_parameters(cls: type | GenericAlias):
    orig = get_origin_robust(cls) or cls
    assert isinstance(orig, type)
    # A PEP 695 class carries authoritative ``__type_params__``; trust it. Only an
    # old-style ``Generic[T]`` class (empty ``__type_params__``) needs the derived
    # ``__parameters__``. This ordering matters mid-creation: during a descriptor's
    # ``__set_name__`` a subclass transiently exposes its *base's* inherited
    # ``__parameters__`` -- a different, often shorter list (acutely so with a
    # ``TypeVarTuple``) -- so ``__type_params__`` is the only reliable source then.
    if orig.__type_params__:
        return orig.__type_params__
    return getattr(orig, "__parameters__", get_args_robust(cls))


def get_num_typevars(cls: type | GenericAlias) -> int:
    return len(get_parameters(cls))


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
