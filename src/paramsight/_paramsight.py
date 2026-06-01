import collections.abc as cabc
import operator
import typing
from functools import cache, reduce
from types import GenericAlias, UnionType, get_original_bases
from typing import Any

from paramsight.type_utils import (
    TypeVar,
    _get_typevar_default,
    _is_typevar,
    coerce_to_type_form,
    get_args_robust,
    get_origin_robust,
    get_parameters,
    is_generic_alias,
)

_NODEFAULT = typing.NoDefault


def _raise_unsupported_type_form(value: Any, origin: Any) -> None:
    raise TypeError(
        f"paramsight: can't substitute typevars inside {value!r} -- it is a "
        f"parameterized type form (origin {origin!r}) that paramsight has no rule "
        f"to rebuild. Supported: class-based generics, unions, and typing special "
        f"forms exposing ``copy_with`` (Callable, Annotated, ...). If this form "
        f"should be supported, please file an issue."
    )


def _substitute_typevars(value: Any, subs: dict[TypeVar, Any]) -> Any:
    """Recursively substitute typevars inside a type, generic alias, union, or
    typing special form (``Callable``, ``Annotated``, ...).

    Reconstruction is per-kind because Python exposes no single rebuild hook:
    unions go through ``|`` (there is no ``UnionType.__class_getitem__``);
    ``Callable`` is rebuilt explicitly from its ``([params], ret)`` shape; other
    non-class special forms (``Annotated``, ...) through their ``copy_with`` over
    the flat internal ``__args__``; and plain class-based generics -- plus
    pydantic models -- through ``origin[...]``. A parameterized form matching none
    of these is refused loudly rather than silently corrupted.
    """
    if value is None:
        return None
    if _is_typevar(value):
        return subs.get(value, value)
    if not (is_generic_alias(value) or isinstance(value, UnionType)):
        return value

    raw_origin = typing.get_origin(value)

    # Unions -- concrete ``int | T`` (UnionType) and the ``typing.Union`` form a
    # typevar produces both land here. Rebuild with ``|``, collapsing now-identical
    # members (``int | int`` -> ``int``). A free typevar member is fine, but the
    # ``NoDefault`` sentinel isn't a type and can't be ``|``-ed, so a union with an
    # unresolved member is itself unresolved.
    if isinstance(value, UnionType) or raw_origin in (typing.Union, UnionType):
        args = get_args_robust(value)
        new_args = tuple(_substitute_typevars(a, subs) for a in args)
        if new_args == args:
            return value
        if any(a is _NODEFAULT for a in new_args):
            return _NODEFAULT
        return reduce(operator.or_, new_args)

    # Callable -- its args are ``([param, ...], ret)`` (or ``(Ellipsis, ret)`` /
    # ``(ParamSpec, ret)``). The nested parameter list defeats both a flat walk
    # and ``__class_getitem__``, so rebuild ``Callable[[params], ret]`` explicitly.
    # ``get_args`` normalizes the ``typing.Callable`` and ``collections.abc.Callable``
    # spellings to this same shape (only the former carries ``copy_with``).
    if raw_origin is cabc.Callable:
        params, ret = get_args_robust(value)
        new_ret = _substitute_typevars(ret, subs)
        if isinstance(params, list):
            new_params = [_substitute_typevars(p, subs) for p in params]
        else:
            new_params = _substitute_typevars(params, subs)  # Ellipsis / ParamSpec
        if new_params == params and new_ret == ret:
            return value
        return cabc.Callable[new_params, new_ret]  # type: ignore[valid-type]

    # Other typing special forms with a non-class origin (``Annotated``,
    # ``ClassVar``, ``Final``, ``Literal``). They expose ``copy_with``, which
    # rebuilds the form from its flat internal ``__args__``. ``get_origin_robust``
    # asserts a class origin and would reject these, so handle them *before*
    # falling through to it. Anything else parameterized is refused loudly.
    if raw_origin is not None and not isinstance(raw_origin, type):
        if not hasattr(value, "copy_with"):
            _raise_unsupported_type_form(value, raw_origin)
        internal = value.__args__
        new_internal = tuple(_substitute_typevars(a, subs) for a in internal)
        if new_internal == internal:
            return value
        return value.copy_with(new_internal)

    # Plain class-based generics (``list[T]``, ``dict``, ``tuple``, ``type``, user
    # generics) and pydantic models -- reconstruct through ``origin[...]``;
    # ``get_args_robust`` / ``get_origin_robust`` carry the pydantic special-case.
    origin = get_origin_robust(value)
    if origin is None:
        return value
    args = get_args_robust(value)
    new_args = tuple(_substitute_typevars(a, subs) for a in args)
    if new_args == args:
        return value
    subscript = new_args[0] if len(new_args) == 1 else new_args
    return origin.__class_getitem__(subscript)  # type: ignore[attr-defined]


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
            # Resolve the arg against the bindings accumulated so far. Usually a
            # no-op, but a PEP 696 default that Python auto-filled into
            # ``__args__`` arrives as the *raw, unsubstituted* typevar -- ``C[int]``
            # on ``class C[T, U = T]`` yields args ``(int, T)`` -- and that ``T``
            # must resolve to ``int``. (Params are walked in declaration order and
            # PEP 696 only lets a default reference *earlier* params, so the
            # referenced binding is always already present.)
            subs[p] = _substitute_typevars(args[i], subs)
            continue
        default = _get_typevar_default(p)
        if default is not _NODEFAULT:
            # Normalize the default the way subscription normalizes an explicit
            # arg (``None`` -> ``NoneType``, ``"Foo"`` -> ``ForwardRef('Foo')``),
            # so ``C`` and ``C[default]`` agree (see ``coerce_to_type_form``),
            # *then* resolve any typevars the default references against the
            # bindings accumulated so far. With ``class C[T, U = T]`` and
            # ``C[int]``, ``U``'s default ``T`` must become ``int`` rather than
            # leak the raw typevar. Params are walked in declaration order, so
            # earlier bindings (here ``T``) are already in ``subs``. Coerce
            # *before* substituting: ``_substitute_typevars`` passes ``None``
            # straight through, so ``None`` must already be ``NoneType``.
            subs[p] = _substitute_typevars(coerce_to_type_form(default), subs)
            continue
        if return_bound_as_fallback:
            bound = getattr(p, "__bound__", None)
            if bound is not None:
                # Same treatment as the default branch: a bound is stored in
                # source form too (``T: "Foo"`` -> the bare string) and may
                # likewise reference an earlier typevar.
                subs[p] = _substitute_typevars(coerce_to_type_form(bound), subs)
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
        return tuple(subs[p] if _is_typevar(p) and p in subs else p for p in params)

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
