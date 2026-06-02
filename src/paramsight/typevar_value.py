"""Property-style descriptors exposing a class's resolved typevars.

Four surfaces share one resolution engine:

- :class:`TypeVarValue` -- statically ``type[T]``. An unresolved typevar with no
  default is treated as an *error* and raises ``LookupError``.
- :class:`TypeVarValueOption` -- statically ``type[T] | None``. An unresolved
  typevar with no default is a *value*: the attribute yields ``None``.
- :class:`TypeVarExpression` / :class:`TypeVarExpressionOption` (*experimental*) --
  resolve a whole type *expression* over the class's typevars (and ``Self``),
  not just a single typevar.

Which to reach for: ``TypeVarValue`` when reading the attribute on anything but a
fully-bound class/specialization is a bug (the common case -- no narrowing tax on
the happy path). ``TypeVarValueOption`` when "unresolved" is a state you want to
branch on with the canonical ``if t is None`` idiom.
"""

import typing
from types import UnionType
from typing import Any

from paramsight._paramsight import (
    _collect_typevars,
    _substitute_typevars,
    get_args_at_base,
    get_typevar_value,
)
from paramsight.aliasclassmethod import (
    _install_ga_proxy,
    _raise_slotted_instance_without_storage,
)
from paramsight.slotted_strategies import (
    _has_orig_class_storage,
    _slot_strategy,
    get_orig_class,
)
from paramsight.type_utils import (
    _NODEFAULT,
    _is_paramspec,
    _is_typevar,
    _is_typevartuple,
    get_args_robust,
    get_parameters,
    is_generic_alias,
)


def _is_unresolved(value: Any) -> bool:
    """True when ``value`` is not a usable resolved type.

    That is: the ``NoDefault`` sentinel, a bare (free) typevar, or a generic
    alias / union / special form still carrying either of those among its
    arguments -- ``list[NoDefault]``, ``list[T]``, ``Callable[[T], int]``. The
    recursion is the point: a top-level ``is NoDefault`` test would wave a nested
    hole through (e.g. a default ``U = list[T]`` read off a class where ``T``
    itself never got bound). A ``Callable``'s parameter list arrives as a plain
    ``list``, so that is walked too. A free ``*Ts`` / ``**P`` (TypeVarTuple /
    ParamSpec, e.g. an ``Unpack[Ts]`` whose ``Ts`` never got bound) counts as
    unresolved as well.
    """
    if value is _NODEFAULT:
        return True
    if _is_typevar(value) or _is_typevartuple(value) or _is_paramspec(value):
        return True
    if isinstance(value, list):  # a Callable's ``[params]`` argument
        return any(_is_unresolved(arg) for arg in value)
    if is_generic_alias(value) or isinstance(value, UnionType):
        return any(_is_unresolved(arg) for arg in get_args_robust(value))
    return False


def _raise_unresolved(base: type, name: str, label: str, value: Any) -> None:
    b = base.__name__
    raise LookupError(
        f"paramsight: ``{b}.{name}`` ({label}) did not resolve to a concrete "
        f"type -- it resolved to {value!r}, which is still unbound. This happens "
        f"when a referenced type parameter has no specialization and no usable "
        f"default. Reach it through a (fuller) specialization (e.g. "
        f"``{b}[int].{name}``), or give the parameter a PEP 696 default. If "
        f"'unresolved' is a state you want to handle rather than an error, use the "
        f"matching ``...Option`` descriptor, which yields ``None`` here."
    )


class _TypeVarValueBase:
    """Shared resolution engine for the typevar-value descriptors.

    Holds everything except ``__get__`` -- the two concrete descriptors differ
    only in what they do with an unresolved-and-defaultless typevar (raise vs.
    yield ``None``), and keeping their ``__get__`` methods as independent
    siblings (rather than an override pair) avoids a Liskov return-type clash
    between ``type[T]`` and ``type[T] | None``.
    """

    # Tells paramsight's generic-alias attribute proxy (_GAProxy) to hand us the
    # alias (e.g. ``Box[int]``) as ``owner`` in ``__get__`` rather than the bare
    # origin class. Without this the descriptor would only ever see ``Box``.
    _acm_takes_alias = True

    _base: type
    _name: str
    _typevar: Any

    def __set_name__(self, owner: type, name: str) -> None:
        kind = type(self).__name__
        orig = getattr(self, "__orig_class__", None)
        if orig is None:
            raise TypeError(
                f"{owner.__name__}.{name}: {kind} must be parameterized "
                f"with one of {owner.__name__}'s typevars, "
                f"e.g. `{name} = {kind}[T]()`"
            )
        self._base = owner
        self._name = name
        # Resolve against the concrete descriptor class actually in use, so this
        # works identically across the TypeVarValue/Option/Expression surfaces.
        (arg,) = get_args_at_base(orig, type(self))
        self._bind(arg, owner)
        # Ensure attribute access via a generic alias (Box[int].value_type)
        # routes through the proxy that respects ``_acm_takes_alias``.
        _install_ga_proxy(owner)

    def _bind(self, arg: Any, owner: type) -> None:
        """Validate and capture the descriptor's type argument. The base
        ``TypeVarValue`` shape wants a single typevar of ``owner``; the expression
        descriptors override this for whole type expressions."""
        kind = type(self).__name__
        if not _is_typevar(arg):
            raise TypeError(
                f"{owner.__name__}.{self._name}: {kind}'s type argument must be "
                f"a TypeVar, got {arg!r}"
            )
        # Trust the param list only when it's the reliable PEP 695
        # ``__type_params__``. For an old-style ``Generic`` class
        # ``__parameters__`` isn't set to the class's own until
        # ``__init_subclass__`` runs (after ``__set_name__``) -- a *direct*
        # ``Generic[T]`` shows empty, but a *subclass* like
        # ``class Child(Base[U], Generic[U])`` transiently exposes the base's
        # inherited params, so validating then would reject ``Child``'s own
        # ``U``. Defer to access time in those cases (``get_typevar_value`` raises
        # if the typevar genuinely isn't the owner's).
        params = list(get_parameters(owner))
        if owner.__type_params__ and arg not in params:
            raise TypeError(
                f"{owner.__name__}.{self._name}: {arg!r} is not a typevar of "
                f"{owner.__name__}; its typevars are {params}"
            )
        self._typevar = arg

    def _resolve_owner(self, instance: object | None, owner: type | None) -> type:
        """Pick the class/alias to resolve through. Raises on a slotted instance
        that has lost its alias, mirroring ``_TakesAlias.__get__``."""
        if instance is not None:
            orig = get_orig_class(instance)
            if orig is not None:
                return orig
            cls = type(instance)
            # Raise only when losing the alias loses information -- ``cls`` still
            # has *free* typevars, no ``__orig_class__`` storage, and no opt-in
            # strategy. A non-generic class, or a concrete subclass that already
            # binds its typevars via the MRO (``Concrete(Base[int])``), resolves
            # fine from ``type(instance)`` and must not raise.
            if (
                get_parameters(cls)
                and not _has_orig_class_storage(cls)
                and _slot_strategy(cls) is None
            ):
                _raise_slotted_instance_without_storage(self._name, cls)
            return cls
        if owner is None:
            raise TypeError(
                f"{self._base.__name__}.{self._name}: cannot resolve without a "
                f"class or instance context"
            )
        return owner

    def _resolve_value(self, instance: object | None, owner: type | None) -> Any:
        """The resolved value, or ``typing.NoDefault`` when the typevar is
        unspecialized and has no default."""
        cls = self._resolve_owner(instance, owner)
        return get_typevar_value(cls, self._base, self._typevar)


class TypeVarValue[T](_TypeVarValueBase):
    """Descriptor that resolves the value of one of the owning class's typevars.

    Define it on a generic class, parameterized with one of that class's own
    typevars::

        class Box[T]:
            value_type = TypeVarValue[T]()

    Reading the attribute on a specialization yields the resolved type, and the
    static type is narrowed to ``type[T]``::

        Box[int].value_type        # int, statically type[int]
        Box[str].value_type        # str, statically type[str]
        Box[int]().value_type      # int (works on instances too)

    The descriptor learns its *base* (the class it is defined on) once, in
    ``__set_name__``. Resolution always happens against whatever class or
    specialization you reach the attribute *through* -- so the static type
    (derived from that same receiver) cannot disagree with the runtime answer.
    There is no ``cls2`` argument to wire wrong.

    Inheritance works without ceremony: ``class Child[U](Box[U])`` then
    ``Child[int].value_type`` resolves to ``int``, and a nested specialization
    like ``class Arch[T](ArchBase[Wrap[T]])`` will resolve ``ArchBase``'s
    typevar to ``Wrap[<arg>]`` when the descriptor is defined on ``ArchBase``.

    Notes:
    - A typevar *default* resolves the same as an explicit argument: ``class
      Box[T = None]`` makes ``Box.value_type`` (and ``Box[None].value_type``)
      both ``NoneType``, and ``class Box[T = "Fwd"]`` yields ``ForwardRef('Fwd')``
      from both branches.
    - Reading the attribute where the typevar is *unbound and has no default*
      (e.g. a bare, unspecialized generic class) raises ``LookupError`` -- the
      static type promises ``type[T]``, so there is no value to honestly return.
      Use :class:`TypeVarValueOption` if you want ``None`` instead.
    - The type argument must be a ``TypeVar`` and must be one of the owning
      class's typevars; otherwise ``TypeVarValue`` raises ``TypeError`` at class
      definition time.
    - On a pydantic ``BaseModel``, register the descriptor so pydantic doesn't
      treat it as a field::

          class MyModel[T](BaseModel):
              model_config = ConfigDict(ignored_types=(TypeVarValue,))
              value_type = TypeVarValue[T]()
    """

    def __get__(self, instance: object | None, owner: type | None = None, /) -> type[T]:
        value = self._resolve_value(instance, owner)
        if _is_unresolved(value):
            tv = getattr(self._typevar, "__name__", self._typevar)
            _raise_unresolved(self._base, self._name, f"TypeVarValue[{tv}]", value)
        return value


class TypeVarValueOption[T](_TypeVarValueBase):
    """Like :class:`TypeVarValue`, but typed ``type[T] | None``.

    Identical resolution, with one difference: where ``TypeVarValue`` *raises*
    because the typevar is unbound and has no default, this yields ``None``. That
    makes "unresolved" a first-class, statically-visible state you check with the
    canonical optional idiom::

        class Box[T]:
            value_type = TypeVarValueOption[T]()

        Box[int].value_type        # int, statically type[int] | None
        Box.value_type             # None  (T unbound, no default)

        t = Box[int].value_type
        if t is not None:          # narrows to type[int]
            ...

    The coercion that keeps the default and explicit branches in agreement also
    guarantees this ``None`` is unambiguous: a typevar whose default *is* ``None``
    resolves to ``NoneType`` (a truthy class), never the ``None`` singleton, so
    ``None`` means exactly "no value to resolve" and nothing else.

    The trade-off versus ``TypeVarValue`` is the usual optional tax: every read
    site sees ``type[T] | None`` and must narrow, even where you know the typevar
    is bound. Prefer ``TypeVarValue`` unless you genuinely branch on absence. The
    ``__set_name__`` validation and pydantic ``ignored_types`` note are the same.
    """

    def __get__(
        self, instance: object | None, owner: type | None = None, /
    ) -> type[T] | None:
        value = self._resolve_value(instance, owner)
        if _is_unresolved(value):
            return None
        return value


class _TypeVarExpressionBase(_TypeVarValueBase):
    """Shared engine for the *experimental* expression descriptors.

    Where :class:`TypeVarValue` resolves a single typevar, these resolve a whole
    type *expression* built from the owning class's typevars (plus ``Self``):
    every typevar in the expression is resolved as usual and substituted in.
    """

    _expr: Any

    def _bind(self, arg: Any, owner: type) -> None:
        # The argument is an arbitrary type expression. Restrict it to the owner's
        # own type parameters -- those are the only ones we can resolve from the
        # receiver. (``Self`` is allowed and resolved separately; it isn't a
        # typevar, so ``_collect_typevars`` ignores it.) Only validate eagerly when
        # the params are the reliable PEP 695 ``__type_params__``; during
        # ``__set_name__`` an old-style ``Generic`` subclass can transiently expose
        # its base's inherited params, so defer to access time then (a genuinely
        # foreign typevar still surfaces as unresolved there).
        kind = type(self).__name__
        if owner.__type_params__:
            params = list(get_parameters(owner))
            foreign = [tv for tv in _collect_typevars(arg) if tv not in params]
            if foreign:
                raise TypeError(
                    f"{owner.__name__}.{self._name}: {kind} may reference only "
                    f"{owner.__name__}'s own type parameters (and ``Self``); got "
                    f"foreign {foreign!r}. Its parameters are {params}."
                )
        self._expr = arg

    def _resolve_expression(self, instance: object | None, owner: type | None) -> Any:
        cls = self._resolve_owner(instance, owner)
        # Resolve every one of the owner's typevars from the receiver, then add
        # ``Self`` -> the receiver itself (kept as the alias it was reached through).
        subs: dict[Any, Any] = dict(
            zip(
                get_parameters(self._base),
                get_args_at_base(cls, self._base),
                strict=True,
            )
        )
        subs[typing.Self] = cls
        return _substitute_typevars(self._expr, subs)


class TypeVarExpression[X](_TypeVarExpressionBase):
    """**Experimental.** Resolve a whole type *expression* over the owning class's
    typevars, not just a single typevar.

    Put it on a generic class, parameterized with any type expression built from
    that class's own typevars (and optionally ``Self``)::

        class Box[T]:
            pair = TypeVarExpression[tuple[T, T]]()
            maybe = TypeVarExpression[T | None]()
            holder = TypeVarExpression[T | Self]()

        Box[int].pair       # tuple[int, int]
        Box[str].maybe      # str | None
        Box[int].holder     # int | Box[int]   (``Self`` -> the receiver alias)

    Every typevar in the expression is resolved exactly as :class:`TypeVarValue`
    resolves a single one, then substituted in; ``Self`` resolves to the class or
    specialization the attribute was reached through (kept as an alias if it was
    one). The expression may reference **only** the owning class's own type
    parameters -- otherwise ``TypeVarExpression`` raises ``TypeError`` at class
    definition time, since foreign typevars can't be resolved from the receiver.

    Like :class:`TypeVarValue`, reading it where some referenced parameter is
    unbound (and undefaulted) raises ``LookupError``; use
    :class:`TypeVarExpressionOption` for the ``None``-yielding variant.

    Status: experimental and may change. ``TypeVarValue[T]`` is the
    ``TypeVarExpression[T]`` special case; if this proves solid it may subsume it.
    """

    def __get__(self, instance: object | None, owner: type | None = None, /) -> type[X]:
        value = self._resolve_expression(instance, owner)
        if _is_unresolved(value):
            _raise_unresolved(
                self._base, self._name, f"TypeVarExpression[{self._expr}]", value
            )
        return value


class TypeVarExpressionOption[X](_TypeVarExpressionBase):
    """**Experimental.** Like :class:`TypeVarExpression`, but typed ``type[X] | None``
    and yielding ``None`` (instead of raising) when a referenced parameter is
    unbound. See :class:`TypeVarExpression`; the ``None`` semantics mirror
    :class:`TypeVarValueOption`.
    """

    def __get__(
        self, instance: object | None, owner: type | None = None, /
    ) -> type[X] | None:
        value = self._resolve_expression(instance, owner)
        if _is_unresolved(value):
            return None
        return value
