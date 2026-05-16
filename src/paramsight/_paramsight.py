import typing
from functools import cache
from types import GenericAlias, get_original_bases
from typing import Any

from paramsight.type_utils import (
    TypeVar,
    _get_typevar_default,
    _is_typevar,
    get_args_robust,
    get_origin_robust,
    get_parameters,
    is_generic_alias,
)

_NODEFAULT = typing.NoDefault


def _substitute_typevars(value: Any, subs: dict[TypeVar, Any]) -> Any:
    """Recursively substitute typevars inside a type or generic alias."""
    if value is None:
        return None
    if _is_typevar(value):
        return subs.get(value, value)
    if is_generic_alias(value):
        origin = get_origin_robust(value)
        args = get_args_robust(value)
        new_args = tuple(_substitute_typevars(a, subs) for a in args)
        if new_args == args or origin is None:
            return value
        subscript = new_args[0] if len(new_args) == 1 else new_args
        return origin.__class_getitem__(subscript)  # type: ignore[attr-defined]
    return value


def _build_subs(
    origin: type,
    args: tuple[Any, ...],
    return_bound_as_fallback: bool,
) -> dict[TypeVar, Any]:
    """Bind ``origin``'s typevars to either positional args, defaults, or
    (when requested) bounds. Typevars with no resolution map to NoDefault so
    that they propagate as the unresolved sentinel through nested aliases."""
    subs: dict[TypeVar, Any] = {}
    for i, p in enumerate(get_parameters(origin)):
        if not _is_typevar(p):
            continue
        if i < len(args):
            subs[p] = args[i]
            continue
        default = _get_typevar_default(p)
        if default is not _NODEFAULT:
            subs[p] = default
            continue
        if return_bound_as_fallback:
            bound = getattr(p, "__bound__", None)
            if bound is not None:
                subs[p] = bound
                continue
        subs[p] = _NODEFAULT
    return subs


def _resolve(
    node: Any,
    target_base: type,
    parent_subs: dict[TypeVar, Any],
    return_bound_as_fallback: bool,
) -> tuple[Any, ...] | None:
    """DFS through ``node``'s inheritance hierarchy looking for ``target_base``.

    ``parent_subs`` is the substitution map accumulated from outer levels and
    is applied to ``node``'s args before computing this level's own
    substitution map. When ``origin`` matches ``target_base`` we read the
    target's own typevars out of the freshly-built map.
    """
    if is_generic_alias(node):
        origin = get_origin_robust(node)
        if origin is None:
            return None
        args = tuple(
            _substitute_typevars(a, parent_subs) for a in get_args_robust(node)
        )
    elif isinstance(node, type):
        origin = node
        args = ()
    else:
        return None

    subs = _build_subs(origin, args, return_bound_as_fallback)

    if origin is target_base:
        params = get_parameters(target_base)
        # Builtin generics (list, dict, tuple, collections.abc.*, ...) don't
        # expose their type parameters via __type_params__/__parameters__, so
        # ``params`` is empty even though we arrived via e.g. ``list[list[T]]``.
        # In that case fall back to the (already-substituted) args we walked to.
        if not params and args:
            return args
        return tuple(
            subs[p] if _is_typevar(p) and p in subs else p
            for p in params
        )

    for base in get_original_bases(origin):
        base_origin = get_origin_robust(base) if is_generic_alias(base) else base
        if base_origin is typing.Generic:
            continue
        result = _resolve(base, target_base, subs, return_bound_as_fallback)
        if result is not None:
            return result
    return None


@cache
def get_args_at_base(
    cls: type | GenericAlias,
    target_base: type,
    return_bound_as_fallback: bool = False,
) -> tuple[type | GenericAlias | None, ...]:
    """Resolve ``target_base``'s type parameters as seen from ``cls``.

    Like :func:`typing.get_args`, but evaluated at an ancestor base anywhere
    in ``cls``'s inheritance hierarchy rather than only at the immediate
    generic alias. Returns one entry per typevar of ``target_base``.
    """
    result = _resolve(cls, target_base, {}, return_bound_as_fallback)
    if result is None:
        raise ValueError(
            f"failed to locate target base {target_base!r} in hierarchy of {cls!r}"
        )
    return result


# Legacy alias — prefer ``get_args_at_base``. Same object, so it shares the cache.
get_resolved_typevars_for_base = get_args_at_base


def get_typevar_value(
    cls: type | GenericAlias,
    target_base: type,
    typevar: TypeVar,
    return_bound_as_fallback: bool = False,
) -> Any:
    """Resolve a single, named typevar of ``target_base`` as seen from ``cls``.

    Where :func:`get_args_at_base` returns all of ``target_base``'s args
    positionally, this looks one up by TypeVar identity. Returns the resolved
    value (a type, a generic alias, or ``typing.NoDefault`` when the typevar is
    unspecialized and has no default).

    This is the untyped escape hatch; for a statically-typed surface, see
    :class:`paramsight.TypeVarValue`.
    """
    params = list(get_parameters(target_base))
    try:
        idx = params.index(typevar)
    except ValueError:
        raise ValueError(
            f"{typevar!r} is not a typevar of {target_base!r}; "
            f"its typevars are {params}"
        ) from None
    return get_args_at_base(cls, target_base, return_bound_as_fallback)[idx]
