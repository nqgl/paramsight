import collections.abc as cabc
import operator
import typing
from functools import cache, reduce
from types import GenericAlias, UnionType, get_original_bases
from typing import Any

from paramsight.type_utils import (
    TypeVar,
    _get_typevar_default,
    _is_paramspec,
    _is_typevar,
    _is_typevartuple,
    _is_unpack,
    _unpack_inner,
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


def _substitute_typevars(value: Any, subs: dict[Any, Any]) -> Any:
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
    if _is_paramspec(value):
        # A ParamSpec binds to a parameter list; surface its binding (a tuple of
        # types, ``...``, or -- if unbound -- itself). The Callable branch turns a
        # bound tuple into the ``[p, ...]`` list form.
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

    # Annotated -- ``Annotated[X, *meta]`` reports its *underlying type* ``X`` as
    # ``typing.get_origin`` (a class), so the non-class special-form branch below
    # misses it; and ``copy_with`` would keep ``__metadata__`` verbatim anyway.
    # Substitute the underlying type *and* each metadata element -- a metadata
    # item may itself reference a typevar (``class M[T](B[Annotated[int, T]])``) --
    # then rebuild. ``__metadata__`` uniquely identifies an ``Annotated`` alias.
    if hasattr(value, "__metadata__"):
        base = value.__origin__
        meta = value.__metadata__
        new_base = _substitute_typevars(base, subs)
        new_meta = tuple(_substitute_typevars(m, subs) for m in meta)
        if new_base == base and new_meta == meta:
            return value
        return typing.Annotated[(new_base, *new_meta)]

    # Callable -- its args are ``([param, ...], ret)`` (or ``(Ellipsis, ret)`` /
    # ``(ParamSpec, ret)``). The nested parameter list defeats both a flat walk
    # and ``__class_getitem__``, so rebuild ``Callable[[params], ret]`` explicitly.
    # ``get_args`` normalizes the ``typing.Callable`` and ``collections.abc.Callable``
    # spellings to this same shape (only the former carries ``copy_with``).
    if raw_origin is cabc.Callable:
        params, ret = get_args_robust(value)
        new_ret = _substitute_typevars(ret, subs)
        if isinstance(params, list):
            # ``[p, ...]`` -- substitute each, flattening any ``*Ts`` (PEP 646
            # permits ``Callable[[int, *Ts], R]``).
            new_params = list(_subst_args(tuple(params), subs))
        elif typing.get_origin(params) is typing.Concatenate:
            # ``Concatenate[t1, ..., P]`` -- flatten its prefix and expand its tail.
            new_params = _subst_concatenate(params, subs)
        elif _is_paramspec(params):
            # A ParamSpec bound to a parameter list comes back as a tuple, which
            # Callable wants in ``[p, ...]`` list form; an unbound one stays itself.
            sub = subs.get(params, params)
            new_params = list(sub) if isinstance(sub, tuple) else sub
        else:
            new_params = _substitute_typevars(params, subs)  # ``...``
        if new_params is _NODEFAULT or new_ret is _NODEFAULT:
            # An unresolved parameter list or return type can't be expressed as a
            # Callable (``Callable[NoDefault, int]`` is invalid), so the whole
            # form is unresolved.
            return _NODEFAULT
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
    new_args = _subst_args(args, subs)
    if new_args == args:
        return value
    subscript = new_args[0] if len(new_args) == 1 else new_args
    return origin.__class_getitem__(subscript)  # type: ignore[attr-defined]


def _fixed_unpack_members(x: Any, subs: dict[Any, Any]) -> tuple[Any, ...] | None:
    """The members of a *fixed-length* unpacked tuple -- ``*tuple[int, str]`` (an
    ``__unpacked__`` ``types.GenericAlias``) or ``Unpack[tuple[int, str]]`` --
    substituted and coerced. Returns None for anything that isn't one: a plain
    (non-unpacked) ``tuple[...]``, an unbounded ``*tuple[int, ...]``, or ``*Ts``
    (a ``TypeVarTuple`` unpack, which binds through ``subs`` instead)."""
    if _is_unpack(x):  # ``Unpack[...]`` spelling
        inner = _unpack_inner(x)
    elif getattr(x, "__unpacked__", False):  # ``*tuple[...]`` spelling
        inner = x
    else:
        return None
    if typing.get_origin(inner) is not tuple:  # e.g. ``*Ts`` -> inner is TypeVarTuple
        return None
    members = get_args_robust(inner)
    if any(m is Ellipsis for m in members):  # unbounded ``*tuple[int, ...]``
        return None
    return tuple(_substitute_typevars(coerce_to_type_form(m), subs) for m in members)


def _subst_args(args: tuple[Any, ...], subs: dict[Any, Any]) -> tuple[Any, ...]:
    """Substitute a sequence of type arguments, flattening any unpack whose
    contents are known: a ``*Ts`` bound to a tuple of types (``tuple[int, *Ts,
    str]`` with ``Ts = (a, b)`` -> ``(int, a, b, str)``) or a fixed unpacked tuple
    (``*tuple[int, str]`` / ``Unpack[tuple[int, str]]`` -> ``int, str``). A
    still-unbound ``*Ts`` is left in place (with its inner re-substituted) so it
    reads as unresolved."""
    out: list[Any] = []
    for a in args:
        if _is_unpack(a):
            inner = _unpack_inner(a)
            if (
                _is_typevartuple(inner)
                and inner in subs
                and subs[inner] is not _NODEFAULT
            ):
                out.extend(subs[inner])
                continue
        members = _fixed_unpack_members(a, subs)
        if members is not None:
            out.extend(members)
            continue
        out.append(_substitute_typevars(a, subs))
    return tuple(out)


def _normalize_paramspec_arg(arg: Any, subs: dict[Any, Any]) -> Any:
    """Normalize a ParamSpec binding to a parameter list. The arg can be a
    tuple/list of types, a bare type (``C[int]`` shorthand for ``[int]``), ``...``,
    or a forwarded ParamSpec; produce a tuple of substituted types (passing ``...``
    or a ParamSpec straight through). The Callable branch turns the tuple into the
    ``[p, ...]`` list form."""
    if arg is Ellipsis or _is_paramspec(arg):
        return arg
    # Coerce each member the way subscription coerces scalar args (``None`` ->
    # ``NoneType``, ``"Foo"`` -> ``ForwardRef``) so the explicit list spelling
    # ``C[[None, "Foo"]]`` agrees with the shorthand ``C[None, "Foo"]``.
    if isinstance(arg, (tuple, list)):
        return tuple(_substitute_typevars(coerce_to_type_form(a), subs) for a in arg)
    return (_substitute_typevars(coerce_to_type_form(arg), subs),)


def _subst_concatenate(conc: Any, subs: dict[Any, Any]) -> Any:
    """Substitute a ``Concatenate[t1, ..., P]`` Callable parameter spec: flatten any
    ``*Ts`` in the prefix and expand the trailing ParamSpec. If that ParamSpec
    resolved to a concrete parameter list the whole thing collapses to a flat list;
    otherwise a Concatenate is rebuilt around the still-symbolic tail."""
    *prefix, tail = get_args_robust(conc)
    new_prefix = list(_subst_args(tuple(prefix), subs))
    if _is_paramspec(tail):
        tail_sub = subs.get(tail, tail)
    else:
        tail_sub = _substitute_typevars(tail, subs)
    if isinstance(tail_sub, tuple):  # ParamSpec -> a concrete parameter list
        return new_prefix + list(tail_sub)
    if tail_sub is Ellipsis:
        # ``Concatenate[int, ...]`` is a valid parameter spec: keep the resolved
        # prefix. Only a prefix-less tail collapses to a bare ``...``.
        if new_prefix:
            return typing.Concatenate[tuple(new_prefix + [...])]
        return Ellipsis
    if _is_paramspec(tail_sub):  # still symbolic -> rebuild a Concatenate
        return typing.Concatenate[tuple(new_prefix + [tail_sub])]
    # The tail didn't resolve to a usable parameter list (e.g. an unbound
    # ParamSpec -> NoDefault); Concatenate's last slot must be a ParamSpec or
    # ``...``, so the whole spec is unresolved.
    return _NODEFAULT


def _typevartuple_default_members(
    default: Any, subs: dict[Any, Any]
) -> tuple[Any, ...] | None:
    """The fixed members of a ``TypeVarTuple`` default (``*Ts = *tuple[int, str]``
    -> ``(int, str)``), substituted against ``subs``. Returns None when the
    default isn't a fixed-length unpacked tuple (e.g. an unbounded
    ``*tuple[int, ...]``), which we can't expand into an absorbed run.

    The ``*tuple[...]`` spelling stores the tuple alias directly; the
    ``Unpack[tuple[...]]`` spelling wraps it -- unwrap that first."""
    if _is_unpack(default):
        default = _unpack_inner(default)
    if typing.get_origin(default) is not tuple:
        return None
    members = get_args_robust(default)
    if any(m is Ellipsis for m in members):
        return None
    # Coerce members the way subscription does (``None`` -> ``NoneType``,
    # ``"Foo"`` -> ``ForwardRef``) so a fixed default matches the explicit
    # specialization ``C[None, "Foo"]``, then resolve any typevars they reference.
    return tuple(_substitute_typevars(coerce_to_type_form(m), subs) for m in members)


def _build_subs(
    origin: type,
    args: tuple[Any, ...],
    return_bound_as_fallback: bool,
    subscripted: bool,
) -> dict[Any, Any]:
    """Bind ``origin``'s type parameters to positional args, defaults, or (when
    requested) bounds. Parameters with no resolution map to NoDefault so that
    they propagate as the unresolved sentinel through nested aliases.

    A ``TypeVarTuple`` (``*Ts``) is variadic: it absorbs the *middle* args, with
    the ordinary parameters before and after it binding around the absorbed run
    (``class C[T, *Ts, U]`` with ``C[int, str, bytes, float]`` gives ``T=int``,
    ``Ts=(str, bytes)``, ``U=float``). ``ParamSpec`` binds positionally like an
    ordinary parameter, to a single parameter-list arg.

    ``subscripted`` says whether we arrived via a subscription (``origin[...]``,
    including an explicitly empty ``origin[()]``) rather than a bare, unspecialized
    class. It only matters for a TypeVarTuple, which can legitimately bind to zero
    types: ``C[()]`` makes ``*Ts`` empty, but bare ``C`` leaves it *unresolved*.
    """
    subs: dict[Any, Any] = {}
    params = list(get_parameters(origin))

    def bind_positional(p: Any, arg: Any) -> None:
        # A ParamSpec's arg is a *parameter list*, not an ordinary type form, so it
        # needs its own normalization (``C[int, ...]`` shorthand -> ``(int,)``;
        # substitute the members of a ``[T]``-style binding).
        if _is_paramspec(p):
            subs[p] = _normalize_paramspec_arg(arg, subs)
            return
        # Resolve the arg against bindings so far. Usually a no-op, but a PEP 696
        # default Python auto-filled into ``__args__`` arrives raw: as the
        # *unsubstituted* typevar (``C[int]`` on ``class C[T, U = T]`` yields args
        # ``(int, T)``, so ``T`` must resolve to ``int``) or as a raw value default
        # (``U = None`` -> ``(int, None)``; ``U = "Foo"`` -> ``(int, "Foo")``).
        # Explicit args are already normalized by subscription, but auto-filled
        # ones are not -- so coerce first, exactly like the default branch, so
        # ``C[int]`` and ``C[int, <default>]`` agree (``None`` -> ``NoneType``,
        # ``"Foo"`` -> ``ForwardRef``). ``coerce_to_type_form`` is a no-op on
        # types/aliases/typevars, so the ``U = T`` case still resolves.
        subs[p] = _substitute_typevars(coerce_to_type_form(arg), subs)

    def bind_unfilled(p: Any) -> None:
        # No positional arg: fall back to default, then (optionally) bound, then
        # the unresolved sentinel.
        default = _get_typevar_default(p)
        if default is not _NODEFAULT:
            if _is_typevartuple(p):
                # A TypeVarTuple's default is an unpacked tuple (``*Ts =
                # *tuple[int, str]``); bind its fixed members so it expands like an
                # absorbed arg run. A non-fixed default (``*tuple[int, ...]``) can't
                # be expanded that way, so leave it unresolved rather than corrupt.
                members = _typevartuple_default_members(default, subs)
                subs[p] = members if members is not None else _NODEFAULT
                return
            if _is_paramspec(p):
                # A ParamSpec default is a parameter list (``**P = [T]``, ``...``,
                # or another ParamSpec), not a scalar type form; normalize it the
                # same way an explicit ParamSpec arg is normalized so it resolves
                # against earlier bindings (``[T]`` -> ``[int]`` when ``T = int``).
                subs[p] = _normalize_paramspec_arg(default, subs)
                return
            # Normalize the default the way subscription normalizes an explicit
            # arg (``None`` -> ``NoneType``, ``"Foo"`` -> ``ForwardRef('Foo')``),
            # then resolve any typevars it references against the bindings so far.
            # Coerce *before* substituting: ``_substitute_typevars`` passes
            # ``None`` straight through, so ``None`` must already be ``NoneType``.
            subs[p] = _substitute_typevars(coerce_to_type_form(default), subs)
            return
        if return_bound_as_fallback:
            bound = getattr(p, "__bound__", None)
            if bound is not None:
                subs[p] = _substitute_typevars(coerce_to_type_form(bound), subs)
                return
        subs[p] = _NODEFAULT

    def bindable(p: Any) -> bool:
        return _is_typevar(p) or _is_paramspec(p)

    tvt_idx = next((i for i, p in enumerate(params) if _is_typevartuple(p)), None)

    if tvt_idx is None:
        for i, p in enumerate(params):
            if not bindable(p):
                continue
            bind_positional(p, args[i]) if i < len(args) else bind_unfilled(p)
        return subs

    # ``*Ts`` present: the ordinary params before it bind to the leading args, the
    # ones after it to the trailing args, and it absorbs whatever is left between.
    n_suffix = len(params) - tvt_idx - 1
    n_absorbed = max(0, len(args) - (len(params) - 1))
    for i in range(tvt_idx):
        if bindable(params[i]):
            bind_positional(params[i], args[i]) if i < len(args) else bind_unfilled(
                params[i]
            )
    tvt = params[tvt_idx]
    if subscripted and len(args) >= len(params) - 1:
        absorbed = tuple(
            _substitute_typevars(a, subs) for a in args[tvt_idx : tvt_idx + n_absorbed]
        )
        if absorbed == ((),):
            # CPython's explicit empty-TypeVarTuple marker: ``C[int, ()]`` puts a
            # bare ``()`` in ``__args__`` for an empty ``*Ts``. That means zero
            # absorbed types, not a one-tuple containing the empty tuple.
            absorbed = ()
        if any(_is_unpack(a) or a is _NODEFAULT for a in absorbed):
            # An absorbed ``*Us`` whose TypeVarTuple stayed unbound (or a NoDefault
            # member) leaves the run's length indeterminate, so the whole binding
            # is unresolved -- not a concrete tuple that merely contains the hole.
            subs[tvt] = _NODEFAULT
        else:
            subs[tvt] = absorbed
    else:
        # Bare (unsubscripted) class, or too few args: ``*Ts`` is unspecified, so
        # treat it as unfilled (-> unresolved) rather than an empty binding,
        # matching how a bare ``C[T]`` leaves ``T`` unresolved.
        bind_unfilled(tvt)
    for j in range(n_suffix):
        p = params[tvt_idx + 1 + j]
        if not bindable(p):
            continue
        arg_idx = tvt_idx + n_absorbed + j
        bind_positional(p, args[arg_idx]) if arg_idx < len(args) else bind_unfilled(p)
    return subs


def _resolve(
    node: Any,
    target_base: type,
    parent_subs: dict[Any, Any],
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
        args = _subst_args(get_args_robust(node), parent_subs)
        subscripted = True
    elif isinstance(node, type):
        origin = node
        args = ()
        subscripted = False
    else:
        return None

    subs = _build_subs(origin, args, return_bound_as_fallback, subscripted)

    if origin is target_base:
        params = get_parameters(target_base)
        # Builtin generics (list, dict, tuple, collections.abc.*, ...) don't
        # expose their type parameters via __type_params__/__parameters__, so
        # ``params`` is empty even though we arrived via e.g. ``list[list[T]]``.
        # In that case fall back to the (already-substituted) args we walked to.
        if not params and args:
            return args
        # ``subs.get(p, p)`` (not gated on ``_is_typevar``) so ``*Ts`` / ``**P``
        # params resolve to their bound value too.
        return tuple(subs.get(p, p) for p in params)

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
