from typing import Any, Protocol

import torch.nn as nn
from attrs import define
from pydantic import BaseModel

from paramsight.aliasclassmethod import (
    takes_alias,
)
from paramsight.paramsight import get_resolved_typevars_for_base


class Variations:
    has_init_subclass: bool = True


class ClassMethod(Protocol):
    @classmethod
    def __call__(cls: type[Any], *args: Any, **kwargs: Any) -> Any: ...


class CheckTVCls[T]:
    @takes_alias
    @classmethod
    def check(cls):
        return get_resolved_typevars_for_base(cls, CheckTVCls)


class CheckPlain[T](CheckTVCls[T]): ...


class CheckPlainDefault[T = str](CheckTVCls[T]): ...


class CheckPlainSuper[T](CheckTVCls[T]):
    @takes_alias
    @classmethod
    def check(cls):
        return super().check()


class CheckPlainSuperDuper_2[T1, T2](CheckPlainSuper[T2]):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        return super().check()


class CheckPlainSuperDuper_1[T1, T2](CheckPlainSuper[T1]):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        return super().check()


# CheckPlain[int]._gaproxy_alias


class CheckPlain2[T1, T2](CheckPlain[T2]): ...


class CheckBasicBaseModel[T](BaseModel):
    @takes_alias
    @classmethod
    def check(cls):
        print("checked", cls)
        assert type(cls) is not type


class NonGenericBaseModel(BaseModel):
    t: int = 2

    @takes_alias
    @classmethod
    def check(cls):
        return get_resolved_typevars_for_base(cls, NonGenericBaseModel)


class CheckBaseModel[T](BaseModel):
    @takes_alias
    @classmethod
    def check(cls):
        return get_resolved_typevars_for_base(cls, CheckBaseModel)


class CheckBaseModel2[T](BaseModel, CheckTVCls[T]):
    field: T


@define
class CheckAttrs[T](CheckTVCls[T]):
    ...
    # field: T


class CheckTorch[T](nn.Module, CheckTVCls[T]): ...


# Simple.__parameters__
# Simple.__args__
# BaseModelWithNothing[int].__type_params__
# BaseModelWithNothing.__parameters__
# BaseModelWithNothing.__type_params__
# BaseModelWithNothing.__parameters__
# CheckPlain.__type_params__
# CheckPlain.__parameters__


def check_cls():
    CheckBaseModel.__class_getitem__(int)
    CheckBaseModel

    type(CheckPlain2[int, str])
    # issubclass(CheckPlain[int], typing._GenericAlias)
    assert CheckPlain[int].check() == (int,)
    assert CheckPlain2[int, float].check() == (float,)
    assert CheckBaseModel[int].check() == (int,)
    assert CheckBaseModel2[int].check() == (int,)
    assert CheckPlainSuperDuper_1[float, str].check() == (float,)
    assert CheckPlainSuperDuper_2[float, str].check() == (str,)

    assert CheckPlainDefault.check() == (str,)
    assert CheckPlainDefault[int].check() == (int,)
    assert CheckPlainSuper[int].check() == (int,)
    assert CheckPlainSuperDuper_1[int, str].check() == (int,)
    assert CheckPlainSuperDuper_2[int, str].check() == (str,)

    assert CheckAttrs[int].check() == (int,)
    assert CheckTorch[int].check() == (int,)

    assert CheckBaseModel[int]().check() == (int,)
    assert CheckBaseModel2[int](field=1).check() == (int,)
    assert CheckPlain[int]().check() == (int,)
    assert CheckPlainDefault().check() == (str,)
    assert CheckPlainDefault[int]().check() == (int,)
    assert CheckPlainSuper[int]().check() == (int,)
    assert CheckPlainSuperDuper_1[int, str]().check() == (int,)
    assert CheckPlainSuperDuper_2[int, str]().check() == (str,)

    assert CheckAttrs[int]().check() == (int,)
    assert CheckPlain2[int, float]().check() == (float,)
    assert CheckTorch[int]().check() == (int,)
    import typing

    assert CheckPlain2.check() == (typing.NoDefault,)
    assert CheckBaseModel.check() == (typing.NoDefault,)
    assert CheckBaseModel2.check() == (typing.NoDefault,)
    assert CheckPlain.check() == (typing.NoDefault,)
    assert CheckPlainSuper.check() == (typing.NoDefault,)
    assert CheckPlainSuperDuper_1.check() == (typing.NoDefault,)
    assert CheckPlainSuperDuper_2.check() == (typing.NoDefault,)

    assert CheckAttrs.check() == (typing.NoDefault,)
    assert CheckTorch.check() == (typing.NoDefault,)
    assert CheckPlain2.check() == (typing.NoDefault,)
    assert CheckBaseModel.check() == (typing.NoDefault,)
    assert CheckBaseModel2(field=1).check() == (typing.NoDefault,)
    assert CheckPlain.check() == (typing.NoDefault,)
    assert CheckPlainSuper.check() == (typing.NoDefault,)
    assert CheckPlainSuperDuper_1.check() == (typing.NoDefault,)
    assert CheckPlainSuperDuper_2.check() == (typing.NoDefault,)

    assert CheckAttrs.check() == (typing.NoDefault,)
    assert CheckTorch.check() == (typing.NoDefault,)


def main():
    check_cls()


if __name__ == "__main__":
    main()
