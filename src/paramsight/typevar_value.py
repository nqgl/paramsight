"""Property-style descriptors exposing the resolved value of a class's typevar.

Two surfaces share one resolution engine:

- :class:`TypeVarValue` -- statically ``type[T]``. An unresolved typevar with no
  default is treated as an *error* and raises ``LookupError``.
- :class:`TypeVarValueOption` -- statically ``type[T] | None``. An unresolved
  typevar with no default is a *value*: the attribute yields ``None``.

Which to reach for: ``TypeVarValue`` when reading the attribute on anything but a
fully-bound class/specialization is a bug (the common case -- no narrowing tax on
the happy path). ``TypeVarValueOption`` when "unresolved" is a state you want to
branch on with the canonical ``if t is None`` idiom.
"""

from types import UnionType
from typing import Any

from paramsight._paramsight import get_args_at_base, get_typevar_value
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
    _is_typevar,
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
    ``list``, so that is walked too.
    """
    if value is _NODEFAULT:
        return True
    if _is_typevar(value):
        return True
    if isinstance(value, list):  # a Callable's ``[params]`` argument
        return any(_is_unresolved(arg) for arg in value)
    if is_generic_alias(value) or isinstance(value, UnionType):
        return any(_is_unresolved(arg) for arg in get_args_robust(value))
    return False


def _raise_unresolved(base: type, name: str, typevar: Any, value: Any) -> None:
    tv = getattr(typevar, "__name__", typevar)
    b = base.__name__
    raise LookupError(
        f"paramsight: ``{b}.{name}`` (TypeVarValue[{tv}]) did not resolve to a "
        f"concrete type -- it resolved to {value!r}, which is still unbound. This "
        f"happens when {tv} has no specialization and no usable default, or when "
        f"its default/binding itself references a typevar that never got bound. "
        f"Reach it through a (fuller) specialization (e.g. ``{b}[int].{name}``), "
        f"or give the typevar a PEP 696 default. If 'unresolved' is a state you "
        f"want to handle rather than an error, swap ``TypeVarValue`` for "
        f"``TypeVarValueOption``, which yields ``None`` here."
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
        # Resolve against the concrete descriptor class actually in use, so this
        # works identically for TypeVarValue and TypeVarValueOption.
        (typevar,) = get_args_at_base(orig, type(self))
        if not _is_typevar(typevar):
            raise TypeError(
                f"{owner.__name__}.{name}: {kind}'s type argument must be "
                f"a TypeVar, got {typevar!r}"
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
            _raise_unresolved(self._base, self._name, self._typevar, value)
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
