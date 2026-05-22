"""A property-style descriptor exposing the resolved value of a class's typevar.

See :class:`TypeVarValue`.
"""

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
from paramsight.type_utils import _is_typevar, get_parameters


class TypeVarValue[T]:
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
    specialization you reach the attribute *through* — so the static type
    (derived from that same receiver) cannot disagree with the runtime answer.
    There is no ``cls2`` argument to wire wrong.

    Inheritance works without ceremony: ``class Child[U](Box[U])`` then
    ``Child[int].value_type`` resolves to ``int``, and a nested specialization
    like ``class Arch[T](ArchBase[Wrap[T]])`` will resolve ``ArchBase``'s
    typevar to ``Wrap[<arg>]`` when the descriptor is defined on ``ArchBase``.

    Notes:
    - On a *bare, unspecialized* generic class with no typevar default, reading
      the attribute yields ``typing.NoDefault`` (the library's unresolved
      sentinel), matching :func:`paramsight.get_args_at_base`.
    - The type argument must be a ``TypeVar`` and must be one of the owning
      class's typevars; otherwise ``TypeVarValue`` raises ``TypeError`` at class
      definition time.
    - On a pydantic ``BaseModel``, register the descriptor so pydantic doesn't
      treat it as a field::

          class MyModel[T](BaseModel):
              model_config = ConfigDict(ignored_types=(TypeVarValue,))
              value_type = TypeVarValue[T]()
    """

    # Tells paramsight's generic-alias attribute proxy (_GAProxy) to hand us the
    # alias (e.g. ``Box[int]``) as ``owner`` in ``__get__`` rather than the bare
    # origin class. Without this the descriptor would only ever see ``Box``.
    _acm_takes_alias = True

    _base: type
    _name: str
    _typevar: Any

    def __set_name__(self, owner: type, name: str) -> None:
        orig = getattr(self, "__orig_class__", None)
        if orig is None:
            raise TypeError(
                f"{owner.__name__}.{name}: TypeVarValue must be parameterized "
                f"with one of {owner.__name__}'s typevars, "
                f"e.g. `{name} = TypeVarValue[T]()`"
            )
        (typevar,) = get_args_at_base(orig, TypeVarValue)
        if not _is_typevar(typevar):
            raise TypeError(
                f"{owner.__name__}.{name}: TypeVarValue's type argument must be "
                f"a TypeVar, got {typevar!r}"
            )
        # Best-effort: for PEP 695 generics the typevars are visible already;
        # for old-style ``Generic[T]`` classes ``__parameters__`` isn't set
        # until ``__init_subclass__`` runs (after ``__set_name__``), so an empty
        # result here just means "check later" — ``get_typevar_value`` will
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

    def __get__(self, instance: object | None, owner: type | None = None, /) -> type[T]:
        if instance is not None:
            orig = get_orig_class(instance)
            if orig is not None:
                cls = orig
            else:
                cls = type(instance)
                # Mirror of ``_TakesAlias.__get__``: raise only when losing the
                # alias loses information -- ``cls`` still has *free* typevars,
                # has no ``__orig_class__`` storage, and no opt-in strategy.
                # A non-generic class, or a concrete subclass that already binds
                # its typevars via the MRO (``Concrete(Base[int])``), resolves
                # fine from ``type(instance)`` and must not raise.
                if (
                    get_parameters(cls)
                    and not _has_orig_class_storage(cls)
                    and _slot_strategy(cls) is None
                ):
                    _raise_slotted_instance_without_storage(self._name, cls)
        else:
            cls = owner
        if cls is None:
            raise TypeError(
                f"{self._base.__name__}.{self._name}: cannot resolve without a "
                f"class or instance context"
            )
        return get_typevar_value(cls, self._base, self._typevar)  # type: ignore[return-value]
