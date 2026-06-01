import inspect
import types
import typing
from collections.abc import Callable
from functools import cache, partial
from types import GenericAlias
from typing import Concatenate, cast, overload

from paramsight._ta_ref_attr import _TA_REF_ATTR
from paramsight.alias_super import _super
from paramsight.ga_proxy import _GAProxy
from paramsight.inject_locals import inject_locals
from paramsight.slotted_strategies import (
    _has_orig_class_storage,
    _slot_strategy,
    get_orig_class,
    is_creating_synth,
)
from paramsight.type_utils import _is_pydantic, get_parameters


def _is_specialized_generic(cls):
    if _is_pydantic(cls):
        return cls.__pydantic_generic_metadata__["origin"] is not None
    if isinstance(
        cls,
        typing._GenericAlias,  # type:ignore[attr-defined]
    ) or isinstance(cls, GenericAlias):
        return True
    if (
        hasattr(cls, "__origin__")
        and hasattr(cls, "__args__")
        and hasattr(cls, "_inst")
        and hasattr(cls, "_name")
    ):
        return True  #  probably should not happen
    return False


def _make_patched_cgi(owner):
    cgi = inspect.getattr_static(owner, "__class_getitem__", None)
    if isinstance(cgi, classmethod):
        cgi = cgi.__func__
    if cgi is not None and getattr(cgi, "__name__", None) == "_patched_cgi":
        # Already patched (e.g. ``attrs.define(slots=True)`` rebuilds the class
        # and re-runs ``__set_name__`` over a namespace that already carries
        # our patched ``__class_getitem__``); don't wrap it again.
        return None
    if cgi is None:
        bound_cgi = getattr(owner, "__class_getitem__", None)
        if bound_cgi is None:
            return None
        cgi = getattr(bound_cgi, "__func__", None)
        if cgi is None:
            cgi = bound_cgi

    _base_cgi = cgi

    def _build(cls, key):
        alias = _base_cgi(cls, key)  # a types.GenericAlias
        assert not _is_pydantic(cls)
        return make_alias_instance_from_alias(_GAProxy, alias)  # our thin wrapper

    @cache
    def _cached(cls, key):
        return _build(cls, key)

    def _patched_cgi(cls, key, _base=_base_cgi):
        assert _base is _base_cgi
        try:
            hash(key)
        except TypeError:
            # ParamSpec subscription -- ``C[[int, str]]`` -- passes an unhashable
            # list key, so memoizing isn't possible; build a fresh proxy. Equal
            # aliases still hash/compare equal, so downstream caches aren't harmed.
            return _build(cls, key)
        return _cached(cls, key)

    return _patched_cgi


def _make_patched_init_subclass(owner):
    _orig_init_subclass = inspect.getattr_static(owner, "__init_subclass__")
    if hasattr(_orig_init_subclass, "__func__"):
        if _orig_init_subclass.__func__.__name__ == "_patched_init_subclass":
            return None

    def _patched_init_subclass(cls, *a, **kw):
        # A class_swap synthetic is an implementation detail: don't fire
        # user/base ``__init_subclass__`` hooks or re-install the proxy on it.
        if is_creating_synth():
            return
        super(owner, cls).__init_subclass__(*a, **kw)
        _install_ga_proxy(cls)
        return

    return _patched_init_subclass


def _raise_slotted_instance_without_storage(method_name, owner):
    o = owner.__name__
    raise TypeError(
        f"paramsight: ``{o}.{method_name}`` was looked up on an instance of "
        f"{o}, which is slotted (attrs / @dataclass(slots=True) / manual "
        f"__slots__) and has no storage for ``__orig_class__``. The instance "
        f"can't carry its parametrization, so the alias is unrecoverable -- "
        f"silently falling back to the unparametrized class would yield "
        f"``NoDefault`` for the typevars, which is almost certainly a bug. "
        f"Opt in with one of paramsight.slotted_strategies:\n\n"
        f"  from paramsight.slotted_strategies import (\n"
        f"      add_field, uses_class_swap, uses_side_table)\n\n"
        f"  # 1. Real ``__orig_class__`` field; survives "
        f"attrs.evolve / copy / pickle.\n"
        f"  #    @add_field must sit *below* @define / @dataclass -- it runs\n"
        f"  #    first, so the field exists before they scan the body.\n"
        f"  @define\n"
        f"  @add_field\n"
        f"  class {o}[T]:\n"
        f"      ...\n\n"
        f"  # 2. Synthetic-subclass swap; no field pollution; survives\n"
        f"  #    evolve / copy / pickle. Trade-off: ``type(inst) is {o}``\n"
        f"  #    becomes False (``isinstance`` still works).\n"
        f"  @uses_class_swap\n"
        f"  @define\n"
        f"  class {o}[T]:\n"
        f"      ...\n\n"
        f"  # 3. Out-of-band table; no field pollution; does NOT survive\n"
        f"  #    evolve / copy / pickle; needs weakref-able instances.\n"
        f"  @uses_side_table\n"
        f"  @define\n"
        f"  class {o}[T]:\n"
        f"      ...\n\n"
        f"  #    ((2) and (3) are equivalent to ``_paramsight_slots =\n"
        f'  #    "class_swap" / "side_table"`` set in the class body.)\n\n'
        f"  # 4. If you only ever call this method class-side (e.g.\n"
        f"  #    ``{o}[int].{method_name}(...)``), no opt-in is needed --\n"
        f"  #    this error fires only at instance lookup time."
    )


def _install_ga_proxy(owner):
    if _is_pydantic(owner):
        return
    if getattr(owner, "_ga_proxy_installed__", None) == owner:
        return
    patched_cgi = _make_patched_cgi(owner)
    if patched_cgi is not None:
        owner.__class_getitem__ = classmethod(patched_cgi)
    patched_init_subclass = _make_patched_init_subclass(owner)
    if patched_init_subclass is not None:
        owner.__init_subclass__ = classmethod(patched_init_subclass)
    owner._ga_proxy_installed__ = owner


class _TakesAlias[T, **P, R](classmethod):
    def __init__(self, func: Callable[Concatenate[type[T], P], R]):
        assert not isinstance(func, classmethod)
        setattr(func, _TA_REF_ATTR, self)
        super().__init__(func)

    def __set_name__(self, owner, name):
        self.name = name
        _install_ga_proxy(owner)

    def __get__(self, instance, owner=None) -> Callable[P, R]:
        if instance is not None:
            orig = get_orig_class(instance)
            if orig is not None:
                owner = orig
            else:
                cls = type(instance)
                # No alias recovered. Raise only if losing the alias actually
                # loses information: ``cls`` must still have *free* typevars.
                # A non-generic class, or a concrete subclass that already
                # binds the base's typevars via its MRO (``Concrete(Base[int])``)
                # resolves fine straight from ``type(instance)`` -- no instance
                # alias is needed. When ``cls`` does have free typevars and is
                # slotted-without-storage and without an opt-in strategy,
                # silently returning ``NoDefault`` for them would mask a real
                # bug, so raise.
                if (
                    get_parameters(cls)
                    and not _has_orig_class_storage(cls)
                    and _slot_strategy(cls) is None
                ):
                    _raise_slotted_instance_without_storage(self.name, cls)
                if owner is None:
                    owner = cls
        return super().__get__(instance, owner)


@overload
def takes_alias[T, **P, R](
    fun_c: Callable[Concatenate[type[T], P], R], *, patch_super: bool = False
) -> Callable[Concatenate[type[T], P], R]: ...


@overload
def takes_alias[T, **P, R](
    fun_c: None = None,
    *,
    patch_super: bool = False,
) -> Callable[
    [Callable[Concatenate[type[T], P], R]], Callable[Concatenate[type[T], P], R]
]: ...


def takes_alias[T, **P, R](
    fun_c: Callable[Concatenate[type[T], P], R] | None = None,
    *,
    patch_super: bool = False,
) -> (
    Callable[Concatenate[type[T], P], R]
    | Callable[
        [Callable[Concatenate[type[T], P], R]], Callable[Concatenate[type[T], P], R]
    ]
):
    if fun_c is None:
        return partial(takes_alias, patch_super=patch_super)

    cm = cast(classmethod, fun_c)
    if not isinstance(cm, classmethod):
        raise ValueError(f"TakesAlias must wrap a classmethod, got {type(cm)} for {cm}")
    func = cm.__func__
    if not patch_super:
        return cast(Callable[Concatenate[type[T], P], R], _TakesAlias(func))
    newfunc = inject_locals(
        super=_super, _decorator_names=["takes_alias", "classmethod"]
    )(func)
    assert isinstance(newfunc, types.FunctionType)
    return cast(Callable[Concatenate[type[T], P], R], _TakesAlias(newfunc))


def make_alias_instance_from_alias(alias_cls, alias):
    return alias_cls(
        origin=alias.__origin__,
        args=alias.__args__,
        inst=alias._inst,
        name=alias._name,
    )
