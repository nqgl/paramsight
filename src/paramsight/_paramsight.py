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
        f"to rebuild. Supported: class-based generics, unions, parameterized "
        f"``type`` aliases, and typing special forms exposing ``copy_with`` "
        f"(Callable, Annotated, ...). If this form should be supported, please "
        f"file an issue."
    )


def _substitute_typevars(value: Any, subs: dict[Any, Any]) -> Any:
    """Recursively substitute typevars inside a type, generic alias, union, or
    typing special form (``Callable``, ``Annotated``, ...).

    Reconstruction is per-kind because Python exposes no single rebuild hook:
    unions go through ``|`` (there is no ``UnionType.__class_getitem__``);
    ``Callable`` is rebuilt explicitly from its ``([params], ret)`` shape;
    parameterized ``type`` aliases by re-subscripting the ``TypeAliasType``;
    other non-class special forms (``Annotated``, ...) through their
    ``copy_with`` over the flat internal ``__args__``; and plain class-based
    generics -- plus pydantic models -- through ``origin[...]``. A parameterized
    form matching none of these is refused loudly rather than silently corrupted.
    """
    if value is None:
        return None
    if value is typing.Self:
        # ``Self`` resolves to the receiver the descriptor was reached through (a
        # class or specialization), supplied in ``subs`` by TypeVarExpression.
        return subs.get(value, value)
    if _is_typevar(value) or _is_paramspec(value):
        # Atomic parameters substitute by identity lookup, defaulting to
        # themselves when unbound. They differ only in the *shape* of the
        # binding -- a TypeVar's is a type; a ParamSpec's is a parameter list
        # (a tuple of types or ``...``), which the Callable branch renders in
        # the ``[p, ...]`` list form.
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

    # A parameterized PEP 695 ``type`` alias -- ``type Pair[T] = tuple[T, T]``
    # used as ``Pair[X]``. Its origin is the TypeAliasType object itself (not a
    # class) and it exposes no ``copy_with``; rebuild by re-subscripting the
    # alias. Keep the alias rather than expanding ``__value__``, so the result
    # prints the way the user wrote it.
    if isinstance(raw_origin, typing.TypeAliasType):
        args = get_args_robust(value)
        new_args = _subst_args(args, subs)
        if new_args == args:
            return value
        if any(a is _NODEFAULT for a in new_args):
            return _NODEFAULT
        return raw_origin[new_args]

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


def _fixed_tuple_members(inner: Any, subs: dict[Any, Any]) -> tuple[Any, ...] | None:
    """The members of a *fixed-length* ``tuple[...]`` alias, coerced the way
    subscription coerces scalar args and substituted -- with any nested bound
    unpack flattened (``tuple[int, *Ts]`` with ``Ts = (a,)`` -> ``(int, a)``).
    Returns None when ``inner`` isn't a tuple alias, or is the unbounded
    ``tuple[X, ...]`` form (no fixed member run to expand)."""
    if typing.get_origin(inner) is not tuple:
        return None
    members = get_args_robust(inner)
    if any(m is Ellipsis for m in members):
        return None
    return _subst_args(tuple(coerce_to_type_form(m) for m in members), subs)


def _unpacked_tuple_alias(x: Any) -> Any | None:
    """The ``tuple[...]`` alias inside an unpacked tuple -- ``*tuple[...]`` (an
    ``__unpacked__`` ``types.GenericAlias``) or ``Unpack[tuple[...]]`` -- or
    None for anything else: a plain (non-unpacked) ``tuple[...]``, or ``*Ts``
    (a ``TypeVarTuple`` unpack, which binds through ``subs`` instead)."""
    if _is_unpack(x):  # ``Unpack[...]`` spelling
        inner = _unpack_inner(x)
    elif getattr(x, "__unpacked__", False):  # ``*tuple[...]`` spelling
        inner = x
    else:
        return None
    if typing.get_origin(inner) is not tuple:  # e.g. ``*Ts`` -> inner is TypeVarTuple
        return None
    return inner


def _fixed_unpack_members(x: Any, subs: dict[Any, Any]) -> tuple[Any, ...] | None:
    """The substituted members of a *fixed-length* unpacked tuple (either
    spelling), or None for anything that isn't one (see the two helpers)."""
    inner = _unpacked_tuple_alias(x)
    if inner is None:
        return None
    return _fixed_tuple_members(inner, subs)


def _restarred_unbounded_unpack(inner: Any, subs: dict[Any, Any]) -> Any:
    """An unbounded tuple unpack (``*tuple[X, ...]``) with ``X`` substituted,
    rebuilt *unpacked*. The plain generic rebuild (``origin[...]``) silently
    drops the star, so rebuild a plain alias and re-star it via the alias's
    ``__iter__``. Always returns the ``*tuple[...]`` spelling, so the
    ``Unpack[tuple[X, ...]]`` input spelling normalizes to it and the two
    yield equal results downstream."""
    new_members = _subst_args(get_args_robust(inner), subs)  # ``...`` passes through
    return next(iter(tuple.__class_getitem__(new_members)))


def _subst_args(args: tuple[Any, ...], subs: dict[Any, Any]) -> tuple[Any, ...]:
    """Substitute a sequence of type arguments.

    Beyond per-element substitution, this knows the sequence-level shapes a
    flat walk would corrupt:

    - a ``*Ts`` unpack whose TypeVarTuple is bound flattens in place
      (``tuple[int, *Ts, str]`` with ``Ts = (a, b)`` -> ``(int, a, b, str)``);
      a still-unbound one is left in place *unchanged* so the result reads as
      unresolved;
    - a fixed unpacked tuple (``*tuple[int, str]`` / ``Unpack[tuple[int,
      str]]``) flattens into its members;
    - an *unbounded* unpacked tuple (``*tuple[X, ...]``) stays one element but
      has ``X`` substituted, rebuilt unpacked and normalized to the
      ``*tuple[...]`` spelling (see ``_restarred_unbounded_unpack``);
    - a plain tuple/list member is a ParamSpec parameter list forwarded
      through a generic alias (``C[[T]]`` stores ``(T,)`` in ``__args__``) or
      the empty-``*Ts`` marker ``()``: substitute its members with this same
      walk (so a bound ``*Ts`` inside the list flattens too), keeping the
      tuple shape for the binding stage to normalize.
    """
    out: list[Any] = []
    for a in args:
        if isinstance(a, (tuple, list)):
            out.append(_subst_args(tuple(a), subs))
            continue
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
        unbounded = _unpacked_tuple_alias(a)
        if unbounded is not None:
            out.append(_restarred_unbounded_unpack(unbounded, subs))
            continue
        out.append(_substitute_typevars(a, subs))
    return tuple(out)


def _collect_typevars(value: Any, out: list[Any] | None = None) -> list[Any]:
    """Every TypeVar / TypeVarTuple / ParamSpec referenced in a type expression,
    in first-seen order. Used to validate that a ``TypeVarExpression`` references
    only its owning class's own type parameters."""
    if out is None:
        out = []
    if _is_typevar(value) or _is_typevartuple(value) or _is_paramspec(value):
        if value not in out:
            out.append(value)
    elif _is_unpack(value):
        _collect_typevars(_unpack_inner(value), out)
    elif isinstance(value, (tuple, list)):
        for a in value:
            _collect_typevars(a, out)
    elif is_generic_alias(value) or isinstance(value, UnionType):
        for a in get_args_robust(value):
            _collect_typevars(a, out)
    return out


def _normalize_paramspec_arg(arg: Any, subs: dict[Any, Any]) -> Any:
    """Normalize a ParamSpec binding to a parameter list. The arg can be a
    tuple/list of types, a bare type (``C[int]`` shorthand for ``[int]``), ``...``,
    or another ParamSpec; produce a tuple of substituted types (passing ``...``
    straight through). The Callable branch turns the tuple into the ``[p, ...]``
    list form."""
    if arg is Ellipsis:
        return arg
    if _is_paramspec(arg):
        # A forwarded ParamSpec resolves through the bindings so far: a PEP 696
        # default may name an earlier ParamSpec (``**Q = P``), and Python
        # auto-fills that default into ``__args__`` as the raw symbol. An
        # unbound one stays itself (still-symbolic, reads as unresolved).
        return subs.get(arg, arg)
    # Coerce each member the way subscription coerces scalar args (``None`` ->
    # ``NoneType``, ``"Foo"`` -> ``ForwardRef``) so the explicit list spelling
    # ``C[[None, "Foo"]]`` agrees with the shorthand ``C[None, "Foo"]``. The
    # unpack-aware walk also flattens a bound ``*Ts`` member (``C[[*Ts]]``).
    if isinstance(arg, (tuple, list)):
        return _subst_args(tuple(coerce_to_type_form(a) for a in arg), subs)
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
    """A ``TypeVarTuple`` default as an absorbed-run binding. A fixed default
    (``*Ts = *tuple[int, str]``) expands to its members (coerced and
    substituted, matching the explicit specialization ``C[int, str]``); an
    unbounded one (``*Ts = *tuple[int, ...]``) binds as a single re-starred
    unpack, matching the explicit ``C[*tuple[int, ...]]``. Returns None when
    the default isn't a tuple alias at all.

    The ``*tuple[...]`` spelling stores the (unpacked) alias directly; the
    ``Unpack[tuple[...]]`` spelling wraps it -- unwrap that first."""
    if _is_unpack(default):
        default = _unpack_inner(default)
    if typing.get_origin(default) is not tuple:
        return None
    fixed = _fixed_tuple_members(default, subs)
    if fixed is not None:
        return fixed
    return (_restarred_unbounded_unpack(default, subs),)


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
                # *tuple[int, str]`` / ``*tuple[int, ...]``); bind it the same
                # way the matching explicit subscription would. A default that
                # isn't a tuple alias at all can't be expanded -> unresolved.
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

    # Positions of still-unexpanded unpacks. After ``_subst_args`` these are
    # exactly the length-indeterminate args: a symbolic ``*Us`` a child
    # forwarded without binding, or an unbounded ``*tuple[X, ...]``. Each
    # stands for an unknown NUMBER of args, so positional anchoring is only
    # sound from the start up to the first one, and from the end back to the
    # last one.
    indet = [
        j
        for j, a in enumerate(args)
        if _is_unpack(a) or getattr(a, "__unpacked__", False)
    ]
    first_indet = indet[0] if indet else len(args)
    last_indet = indet[-1] if indet else -1

    def bind(p: Any, arg_idx: int, *, from_end: bool = False) -> None:
        # One param, one arg slot. Bind positionally when the slot is anchored
        # (no unpack between it and the end it counts from); fall back to
        # default/bound/sentinel when no arg was provided at all; and bind the
        # unresolved sentinel when an arg WAS provided but an unpack makes the
        # slot's content unknowable -- a default would be wrong there, because
        # the subscription overrides it with a value we just can't see.
        if not bindable(p):
            return
        anchored = 0 <= arg_idx < len(args) and (
            arg_idx > last_indet if from_end else arg_idx < first_indet
        )
        if anchored:
            bind_positional(p, args[arg_idx])
        elif indet:
            subs[p] = _NODEFAULT
        else:
            bind_unfilled(p)

    tvt_idx = next((i for i, p in enumerate(params) if _is_typevartuple(p)), None)

    if tvt_idx is None:
        for i, p in enumerate(params):
            bind(p, i)
        return subs

    # ``*Ts`` present: the ordinary params before it bind to the leading args, the
    # ones after it to the trailing args, and it absorbs whatever is left between.
    n_suffix = len(params) - tvt_idx - 1
    n_absorbed = max(0, len(args) - (len(params) - 1))
    for i in range(tvt_idx):
        bind(params[i], i)

    tvt = params[tvt_idx]
    window_ok = all(tvt_idx <= j < tvt_idx + n_absorbed for j in indet)
    if not subscripted:
        # Bare (unsubscripted) class: ``*Ts`` is unspecified, so treat it as
        # unfilled (-> default / unresolved) rather than an empty binding,
        # matching how a bare ``C[T]`` leaves ``T`` unresolved.
        bind_unfilled(tvt)
    elif indet and not window_ok:
        # An unpack outside the absorbed window un-anchors the split itself
        # (the window's boundaries count across the unpack). The subscription
        # DID cover ``*Ts`` -- with something we can't see -> unresolved.
        subs[tvt] = _NODEFAULT
    elif len(args) < len(params) - 1:
        # Too few args to cover the ordinary params (malformed short
        # subscription): ``*Ts`` got nothing -> unfilled.
        bind_unfilled(tvt)
    else:
        absorbed = _subst_args(args[tvt_idx : tvt_idx + n_absorbed], subs)
        if absorbed == ((),):
            # CPython's explicit empty-TypeVarTuple marker: ``C[int, ()]`` puts a
            # bare ``()`` in ``__args__`` for an empty ``*Ts``. That means zero
            # absorbed types, not a one-tuple containing the empty tuple.
            absorbed = ()
        if any(_is_unpack(a) or a is _NODEFAULT for a in absorbed):
            # An absorbed ``*Us`` whose TypeVarTuple stayed unbound (or a NoDefault
            # member) leaves the run's length indeterminate, so the whole binding
            # is unresolved -- not a concrete tuple that merely contains the hole.
            # (An unbounded ``*tuple[X, ...]`` is fine -- it IS the binding, kept
            # whole; ``_subst_args`` normalized it to the star spelling, which
            # ``_is_unpack`` doesn't match.)
            subs[tvt] = _NODEFAULT
        else:
            subs[tvt] = absorbed

    for j in range(n_suffix):
        bind(params[tvt_idx + 1 + j], tvt_idx + n_absorbed + j, from_end=True)
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
    generic alias. Returns one entry per type parameter of ``target_base``;
    the entry's shape depends on the parameter's kind:

    - ``TypeVar`` -> the resolved type form (a type, generic alias, union,
      ``ForwardRef``, ...).
    - ``TypeVarTuple`` (``*Ts``) -> a plain Python **tuple** of the absorbed
      types -- ``()`` when explicitly empty, and possibly containing an
      unbounded ``*tuple[X, ...]`` unpack when bound to one.
    - ``ParamSpec`` (``**P``) -> a plain Python **tuple** of parameter types,
      ``...`` (Ellipsis), or a still-symbolic ParamSpec it was forwarded to.
    - Unresolved (no specialization, no usable default) -> ``typing.NoDefault``.
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
    typevar: TypeVar | Any,
    return_bound_as_fallback: bool = False,
) -> Any:
    """Resolve a single, named type parameter of ``target_base`` as seen from
    ``cls``.

    Where :func:`get_args_at_base` returns all of ``target_base``'s args
    positionally, this looks one up by identity. ``typevar`` may be any kind of
    type parameter -- a ``TypeVar``, a ``TypeVarTuple``, or a ``ParamSpec`` --
    and the returned value takes the per-kind shape documented on
    :func:`get_args_at_base` (``typing.NoDefault`` when unresolved).

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
