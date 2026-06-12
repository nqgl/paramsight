"""Property-style descriptors exposing the resolved value of a class's type
parameters.

One resolution engine, one descriptor pair per parameter kind:

- :class:`TypeVarValue` / :class:`TypeVarValueOption` -- an ordinary ``TypeVar``;
  statically ``type[T]`` (resp. ``type[T] | None``).
- :class:`TypeVarTupleValue` / :class:`TypeVarTupleValueOption` -- a PEP 646
  ``TypeVarTuple`` (``*Ts``); resolves to a plain tuple of types.
- :class:`ParamSpecValue` / :class:`ParamSpecValueOption` -- a PEP 612
  ``ParamSpec`` (``**P``); resolves to a parameter list (``[int, str]``-style
  list, or ``...``).

In each pair, the plain descriptor treats an unresolved-and-defaultless
parameter as an *error* and raises ``LookupError``; the ``...Option`` variant
treats it as a *value* and yields ``None``.

Which to reach for: the plain variant when reading the attribute on anything but
a fully-bound class/specialization is a bug (the common case -- no narrowing tax
on the happy path). The ``...Option`` variant when "unresolved" is a state you
want to branch on with the canonical ``if t is None`` idiom.
"""

from types import EllipsisType, UnionType
from typing import Any

from paramsight._paramsight import get_typevar_value
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
    _is_unpack,
    _unpack_inner,
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
    ``list``, and a ``TypeVarTuple``/``ParamSpec`` binding as a plain ``tuple``,
    so those are walked too. A free ``*Ts`` / ``**P`` (TypeVarTuple / ParamSpec,
    e.g. an ``Unpack[Ts]`` whose ``Ts`` never got bound) counts as unresolved
    as well.
    """
    if value is _NODEFAULT:
        return True
    if _is_typevar(value) or _is_typevartuple(value) or _is_paramspec(value):
        return True
    if isinstance(value, (list, tuple)):  # Callable ``[params]`` / variadic binding
        return any(_is_unresolved(arg) for arg in value)
    if is_generic_alias(value) or isinstance(value, UnionType):
        return any(_is_unresolved(arg) for arg in get_args_robust(value))
    return False


def _raise_unresolved(
    base: type, name: str, typevar: Any, value: Any, kind: str = "TypeVarValue"
) -> None:
    tv = getattr(typevar, "__name__", typevar)
    b = base.__name__
    raise LookupError(
        f"paramsight: ``{b}.{name}`` ({kind}[{tv}]) did not resolve to a "
        f"concrete type -- it resolved to {value!r}, which is still unbound. This "
        f"happens when {tv} has no specialization and no usable default, or when "
        f"its default/binding itself references a typevar that never got bound. "
        f"Reach it through a (fuller) specialization (e.g. ``{b}[int].{name}``), "
        f"or give the typevar a PEP 696 default. If 'unresolved' is a state you "
        f"want to handle rather than an error, swap ``{kind}`` for "
        f"``{kind}Option``, which yields ``None`` here."
    )


class _TypeVarValueBase:
    """Shared resolution engine for the typevar-value descriptors.

    Holds everything except ``__get__`` -- the concrete descriptors differ
    only in which kind of type parameter they accept (``_accept_arg``) and in
    what they do with an unresolved-and-defaultless one (raise vs. yield
    ``None``). The ``__get__`` methods are independent siblings (rather than
    an override chain) to avoid Liskov return-type clashes between surfaces
    like ``type[T]`` and ``type[T] | None``.
    """

    # Tells paramsight's generic-alias attribute proxy (_GAProxy) to hand us the
    # alias (e.g. ``Box[int]``) as ``owner`` in ``__get__`` rather than the bare
    # origin class. Without this the descriptor would only ever see ``Box``.
    _acm_takes_alias = True

    # Subclass knobs: what kind of type parameter the descriptor takes.
    _arg_kind_description = "a TypeVar"
    _example_subscript = "T"

    _base: type
    _name: str
    _typevar: Any

    @staticmethod
    def _accept_arg(arg: Any) -> Any | None:
        """The type-parameter object when ``arg`` is the kind this descriptor
        accepts (unwrapping any spelling, e.g. ``*Ts`` -> the TypeVarTuple),
        else None."""
        return arg if _is_typevar(arg) else None

    def __set_name__(self, owner: type, name: str) -> None:
        kind = type(self).__name__
        orig = getattr(self, "__orig_class__", None)
        if orig is None:
            raise TypeError(
                f"{owner.__name__}.{name}: {kind} must be parameterized "
                f"with one of {owner.__name__}'s type parameters, "
                f"e.g. `{name} = {kind}[{self._example_subscript}]()`"
            )
        # Read the type argument straight off the subscription. (Resolving it
        # through ``get_args_at_base`` would lose a variadic's identity: a
        # symbolic ``*Ts`` arg deliberately binds to the unresolved sentinel,
        # but here the symbol itself IS the answer.)
        args = get_args_robust(orig)
        typevar = self._accept_arg(args[0]) if len(args) == 1 else None
        if typevar is None:
            got = args[0] if len(args) == 1 else args
            raise TypeError(
                f"{owner.__name__}.{name}: {kind}'s type argument must be "
                f"{self._arg_kind_description}, got {got!r}"
            )
        # Best-effort: for PEP 695 generics the typevars are visible already;
        # for old-style ``Generic[T]`` classes ``__parameters__`` isn't set
        # until ``__init_subclass__`` runs (after ``__set_name__``), so an empty
        # result here just means "check later" -- ``get_typevar_value`` will
        # raise at access time if the typevar genuinely isn't the owner's.
        params = list(get_parameters(owner))
        if params and typevar not in params:
            raise TypeError(
                f"{owner.__name__}.{name}: {typevar!r} is not a typevar of "
                f"{owner.__name__}; its typevars are {params}"
            )
        self._base = owner
        self._name = name
        self._typevar = typevar
        # Ensure attribute access via a generic alias (Box[int].value_type)
        # routes through the proxy that respects ``_acm_takes_alias``.
        _install_ga_proxy(owner)

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
            _raise_unresolved(
                self._base, self._name, self._typevar, value, type(self).__name__
            )
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


class _TypeVarTupleValueBase(_TypeVarValueBase):
    """Validation shared by the ``TypeVarTuple`` descriptors: the subscription
    must be a single unpacked TypeVarTuple (``Kind[*Ts]``)."""

    _arg_kind_description = "an unpacked TypeVarTuple (`*Ts`)"
    _example_subscript = "*Ts"

    @staticmethod
    def _accept_arg(arg: Any) -> Any | None:
        if _is_unpack(arg):
            inner = _unpack_inner(arg)
            if _is_typevartuple(inner):
                return inner
        return None


class TypeVarTupleValue[*Ts](_TypeVarTupleValueBase):
    """Descriptor resolving the value of the owning class's ``TypeVarTuple``.

    Define it on a variadic generic class, parameterized with that class's own
    ``*Ts``::

        class Shape[*Ts]:
            dims = TypeVarTupleValue[*Ts]()

        Shape[int, str].dims              # (int, str) -- a plain tuple of types
        Shape[()].dims                    # ()  (explicitly empty)
        Shape[*tuple[int, ...]].dims      # (*tuple[int, ...],)  (unbounded run)

    The runtime value is a plain Python tuple with one entry per absorbed type
    (the same shape :func:`paramsight.get_args_at_base` reports for a
    ``TypeVarTuple`` parameter). The static return type is ``tuple[Any, ...]``
    -- Python's type system has no way to map ``*Ts`` onto a tuple of ``type``
    objects. Reading the attribute where ``*Ts`` is unbound (a bare,
    unsubscripted class with no usable default) raises ``LookupError``; see
    :class:`TypeVarTupleValueOption` for the ``None``-yielding variant.
    """

    def __get__(
        self, instance: object | None, owner: type | None = None, /
    ) -> tuple[Any, ...]:
        value = self._resolve_value(instance, owner)
        if _is_unresolved(value):
            _raise_unresolved(
                self._base, self._name, self._typevar, value, type(self).__name__
            )
        return value


class TypeVarTupleValueOption[*Ts](_TypeVarTupleValueBase):
    """Like :class:`TypeVarTupleValue`, but an unresolved ``*Ts`` yields
    ``None`` instead of raising. The empty binding is unambiguous: an
    explicitly empty ``*Ts`` (``Shape[()]``) resolves to ``()``, never
    ``None``, so ``None`` means exactly "no binding to resolve"."""

    def __get__(
        self, instance: object | None, owner: type | None = None, /
    ) -> tuple[Any, ...] | None:
        value = self._resolve_value(instance, owner)
        if _is_unresolved(value):
            return None
        return value


class _ParamSpecValueBase(_TypeVarValueBase):
    """Validation shared by the ``ParamSpec`` descriptors: the subscription
    must be a single ParamSpec (``Kind[P]``)."""

    _arg_kind_description = "a ParamSpec"
    _example_subscript = "P"

    @staticmethod
    def _accept_arg(arg: Any) -> Any | None:
        return arg if _is_paramspec(arg) else None

    @staticmethod
    def _as_parameter_list(value: Any) -> list[Any] | EllipsisType:
        # A bound ParamSpec is a tuple of parameter types; render it in the
        # ``[p, ...]`` list form ``Callable`` uses. ``...`` stays itself.
        return list(value) if isinstance(value, tuple) else value


class ParamSpecValue[**P](_ParamSpecValueBase):
    """Descriptor resolving the value of the owning class's ``ParamSpec``.

    Define it on a ParamSpec'd generic class, parameterized with that class's
    own ``**P``::

        class Handler[**P]:
            params = ParamSpecValue[P]()

        Handler[[int, str]].params        # [int, str] -- the parameter list
        Handler[int].params               # [int]  (PEP 612 shorthand)
        Handler[...].params               # Ellipsis

    A bound parameter list is returned as a plain Python list (the same shape
    ``Callable[[...], R]`` reports via ``typing.get_args``); a ``...`` binding
    is returned as ``Ellipsis`` itself. Reading the attribute where ``**P`` is
    unbound (a bare, unsubscripted class with no usable default) raises
    ``LookupError``; see :class:`ParamSpecValueOption` for the
    ``None``-yielding variant.
    """

    def __get__(
        self, instance: object | None, owner: type | None = None, /
    ) -> list[Any] | EllipsisType:
        value = self._resolve_value(instance, owner)
        if _is_unresolved(value):
            _raise_unresolved(
                self._base, self._name, self._typevar, value, type(self).__name__
            )
        return self._as_parameter_list(value)


class ParamSpecValueOption[**P](_ParamSpecValueBase):
    """Like :class:`ParamSpecValue`, but an unresolved ``**P`` yields ``None``
    instead of raising. The ``...`` binding is unambiguous: a ParamSpec bound
    to ellipsis resolves to ``Ellipsis`` (a truthy singleton distinct from
    ``None``), so ``None`` means exactly "no binding to resolve"."""

    def __get__(
        self, instance: object | None, owner: type | None = None, /
    ) -> list[Any] | EllipsisType | None:
        value = self._resolve_value(instance, owner)
        if _is_unresolved(value):
            return None
        return self._as_parameter_list(value)
