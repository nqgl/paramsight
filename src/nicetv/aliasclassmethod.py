import inspect
import types
import typing
from collections.abc import Callable
from functools import partial, wraps
from typing import Concatenate, Self, cast

from pydantic import BaseModel

from nicetv._ta_ref_attr import _TA_REF_ATTR
from nicetv.ga_proxy import _GAProxy, _ga_class_fields
from nicetv.alias_super import _super
from nicetv.extract_from_stack_gpt5 import (
    find_generic_alias_in_stack,
)
from nicetv.inject_locals import inject_locals
from nicetv.extract_from_stack_opus41 import extract_generic_context
from nicetv.extract_from_stack_sonnet45 import find_generic_in_stack

from types import new_class
from typing import get_origin, get_args
from weakref import WeakValueDictionary


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


def _is_specialized_generic(cls):
    if _is_pydantic(cls):
        return cls.__pydantic_generic_metadata__["origin"] is not None
    if isinstance(cls, typing._GenericAlias) or isinstance(cls, typing.GenericAlias):
        return True
    if (
        hasattr(cls, "__origin__")
        and hasattr(cls, "__args__")
        and hasattr(cls, "_inst")
        and hasattr(cls, "_name")
    ):
        return True  #  probably should not happen
    return False


def _make_patched_cgi(owner, parent):
    cgi = inspect.getattr_static(owner, "__class_getitem__", None)
    if isinstance(cgi, classmethod):
        cgi = cgi.__func__
    if cgi is None:
        bound_cgi = getattr(owner, "__class_getitem__", None)
        if bound_cgi is None:
            return None
        cgi = getattr(bound_cgi, "__func__", None)
        if cgi is None:
            cgi = bound_cgi

    _base_cgi = cgi

    def _patched_cgi(cls, key, _base=_base_cgi):
        assert _base is _base_cgi
        alias = _base_cgi(cls, key)  # a types.GenericAlias
        assert not _is_pydantic(cls)
        return make_alias_instance_from_alias(_GAProxy, alias)  # our thin wrapper

    return _patched_cgi


def _make_patched_init_subclass(owner):
    _orig_init_subclass = inspect.getattr_static(owner, "__init_subclass__")
    if hasattr(_orig_init_subclass, "__func__"):
        if _orig_init_subclass.__func__.__name__ == "_patched_init_subclass":
            return None

    def _patched_init_subclass(cls, *a, **kw):
        super(owner, cls).__init_subclass__(*a, **kw)
        _install_ga_proxy(cls)
        return

    return _patched_init_subclass


def _install_ga_proxy(owner):
    if _is_pydantic(owner):
        return
    if (parent := getattr(owner, "_ga_proxy_installed__", None)) != owner:
        patched_cgi = _make_patched_cgi(owner, parent)
        if patched_cgi is not None:
            owner.__class_getitem__ = classmethod(patched_cgi)
        patched_init_subclass = _make_patched_init_subclass(owner)
        if patched_init_subclass is not None:
            # setattr(patched_init_subclass, "_is_patched_init_subclass", True)
            # setattr(
            #     patched_init_subclass,
            #     "_original_init_subclass",
            #     owner.__init_subclass__,
            # )
            owner.__init_subclass__ = classmethod(patched_init_subclass)
        owner._ga_proxy_installed__ = owner


class _TakesAlias[T, **P, R](classmethod):
    def __init__(self, func: Callable[Concatenate[T, P], R]):
        assert not isinstance(func, classmethod)
        setattr(func, _TA_REF_ATTR, self)
        super().__init__(func)

    def __set_name__(self, owner, name):
        # self.cm.__set_name__(owner, name)
        self.name = name
        _install_ga_proxy(owner)

    def __get__(self, instance, owner=None) -> Callable[P, R]:
        if instance is not None:
            if hasattr(instance, "__orig_class__"):
                owner = instance.__orig_class__
            if owner is None:
                owner = instance.__class__
        return super().__get__(instance, owner)


def takes_alias[T, **P, R](
    fun_c: Callable[Concatenate[T, P], R],
) -> Callable[Concatenate[T, P], R]:
    cm = cast(classmethod, fun_c)
    if not isinstance(cm, classmethod):
        raise ValueError(f"TakesAlias must wrap a classmethod, got {type(cm)} for {cm}")
    func = cm.__func__
    newfunc = inject_locals(super=_super, _decorator_name="takes_alias")(func)
    assert isinstance(newfunc, types.FunctionType)
    return cast(Callable[Concatenate[T, P], R], _TakesAlias(newfunc))


def make_alias_instance_from_alias(alias_cls, alias):
    return alias_cls(
        origin=alias.__origin__,
        args=alias.__args__,
        inst=alias._inst,
        name=alias._name,
    )
