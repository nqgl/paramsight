import typing
from types import GenericAlias
from typing import Any, TypeVar, reveal_type

from paramsight._paramsight import get_args_at_base
from paramsight.aliasclassmethod import _install_ga_proxy, takes_alias
from paramsight.type_utils import (
    _is_typevar,
    get_parameters,
)


def get_typevar_value[T](
    cls: type | GenericAlias,
    base: type,
    tv: TypeVar,
    return_bound_as_fallback: bool = False,
):
    params = list(get_parameters(base))
    try:
        idx = params.index(tv)
    except ValueError:
        raise ValueError(f"{tv!r} is not a typevar of {base!r}; params are {params}")
    return get_args_at_base(cls, base, return_bound_as_fallback)[idx]


class RuntimeUnpack[T]:
    @takes_alias
    @classmethod
    def _get_t(cls):
        (t,) = get_args_at_base(cls, RuntimeUnpack)
        return t

    @takes_alias
    @classmethod
    def unpack(cls, tgt, base) -> type[T]:
        t = cls._get_t()
        return get_typevar_value(tgt, base, t)  # type: ignore


class SomeClass[T]:
    @takes_alias
    @classmethod
    def check_t(cls) -> type[T]:
        r = RuntimeUnpack[T].unpack(cls, SomeClass)

        reveal_type(r)
        return r

    @classmethod
    def ensure_t(cls, val: Any) -> T:
        assert isinstance(val, cls.check_t())
        return val

    @takes_alias
    @classmethod
    def check_t2_wrong_example(cls, cls2) -> type[T]:
        r = RuntimeUnpack[T].unpack(cls2, SomeClass)
        reveal_type(r)
        return r


"""
RuntimeUnpack is not quite right, because it does not necessarily type correclty as seen
in check_t2_wrong_example -- type checker thinks you have T@cls but in fact you got 
T@cls2. This is addressed below by making it a descriptor so the typevar is bound to the
particular cls getting queried

"""


class TypeVarValue[T]:
    _acm_takes_alias = True  # tells _GAProxy to pass the alias as owner

    def __set_name__(self, owner: type, name: str) -> None:
        self._base = owner
        self._name = name
        orig = getattr(self, "__orig_class__", None)
        if orig is None:
            raise TypeError(
                f"TypeVarGetter at {owner.__name__}.{name} must be parameterized "
                f"with a TypeVar: use TypeVarGetter[T]() inside the class body"
            )
        (tv,) = get_args_at_base(orig, TypeVarValue)
        if not _is_typevar(tv):
            raise TypeError(
                f"TypeVarGetter at {owner.__name__}.{name} must be parameterized "
                f"with a TypeVar, got {tv!r}"
            )
        self._tv = tv
        _install_ga_proxy(owner)  # so descriptor access via alias hits the proxy

    def __get__(self, instance, owner=None) -> type[T]:
        if instance is not None:
            cls = getattr(instance, "__orig_class__", None) or type(instance)
        else:
            cls = owner
        return get_typevar_value(cls, self._base, self._tv)  # type: ignore[return-value]


class SomeClass2[T]:
    t_value = TypeVarValue[T]()

    @takes_alias
    @classmethod
    def ensure_t(cls, val: object) -> T:
        assert isinstance(val, cls.t_value)  # type[T] — works with isinstance
        return val  # type: ignore[return-value]

    def in_method(self):
        return self.t_value

    @takes_alias
    @classmethod
    def in_classmethod(cls):
        t = cls.t_value
        return t


class ChildClass[T2](SomeClass2[T2]):
    @takes_alias
    @classmethod
    def check_inference(cls):
        reveal_type(cls.t_value)


class ChildClassListed[T](SomeClass2[list[T]]):
    @takes_alias
    @classmethod
    def check_inference(cls):
        reveal_type(cls.t_value)


class ChildClass3(SomeClass2[int]):
    @takes_alias
    @classmethod
    def check_inference(cls):
        reveal_type(cls.t_value)


print("pt2")
intsc = SomeClass2[int]
SomeClass2[str]
# t = intsc.get_t
t = SomeClass2[int].t_value
reveal_type(t)
print(t)
print(SomeClass2[str].t_value)
print(intsc().in_method())
print(intsc.in_classmethod())


child1 = ChildClass[int]
child2 = ChildClassListed[int]
child3 = ChildClass3
print(child1.t_value)
print(child2.t_value)
print(child3.t_value)
print(child1().in_method())
print(child2().in_method())
print(child3().in_method())
