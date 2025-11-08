from typing import Any, Protocol

import torch.nn as nn
from attrs import define
from pydantic import BaseModel

from nicetv.aliasclassmethod import (
    aliasclassmethod,
    takes_alias,
)


class Variations:
    has_init_subclass: bool = False


class ClassMethod(Protocol):
    @classmethod
    def __call__(cls: type[Any], *args: Any, **kwargs: Any) -> Any: ...


class CheckCls:
    @takes_alias
    @classmethod
    def check(cls):
        print("checked", cls)
        assert type(cls) is not type
        cls.check_2(2)

    @takes_alias
    @classmethod
    def check_2(cls, arg: int):
        """
        checking that chained aliasclassmethod decorators work
        """
        print("checked 2", cls)
        assert type(cls) is not type

    # @acm
    # def check_3(cls):
    #     print("checked 3", cls)
    #     cls.check_4()

    @classmethod
    def normal_classmethod(cls):
        print("normal classmethod", cls)

    @classmethod
    def normal_classmethod2(cls, arg: int):
        print("normal classmethod", cls)

    if Variations.has_init_subclass:

        def __init_subclass__(cls) -> None:
            print("called __init_subclass__", cls)
            super().__init_subclass__()


class CheckPlain[T](CheckCls): ...


# CheckPlain[int]._gaproxy_alias


class CheckPlain2[T1, T2](CheckPlain[T2]): ...


class CheckBasicBaseModel[T](BaseModel):
    @aliasclassmethod
    def check(cls):
        print("checked", cls)
        assert type(cls) is not type


class CheckBaseModel[T](CheckCls, BaseModel): ...


class CheckBaseModel2[T](BaseModel, CheckCls):
    field: T


@define
class CheckAttrs[T](CheckCls):
    ...
    # field: T


class CheckTorch[T](nn.Module, CheckCls): ...


class DoesNothing: ...


class BaseModelWithNothing[T](BaseModel, DoesNothing): ...


class Simple[T]: ...


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
    CheckPlain2[int, float].check()
    CheckBaseModel[int].check()
    CheckBaseModel2[int].check()
    CheckPlain[int].check()
    CheckAttrs[int].check()
    CheckTorch[int].check()

    CheckBaseModel[int]().check()
    CheckBaseModel2[int](field=1).check()
    CheckPlain[int]().check()
    CheckAttrs[int]().check()
    CheckPlain2[int, float]().check()
    CheckTorch[int]().check()


def main():
    check_cls()


if __name__ == "__main__":
    main()
