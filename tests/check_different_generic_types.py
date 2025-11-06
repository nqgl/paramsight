import torch.nn as nn
from pydantic import BaseModel
from attrs import define
from nicetv.mapytype import aliasclassmethod


class Variations:
    has_init_subclass: bool = False


class CheckCls:
    @aliasclassmethod
    def check(cls):
        print("checked", cls)
        assert type(cls) is not type

    if Variations.has_init_subclass:

        def __init_subclass__(cls) -> None:
            print("called __init_subclass__", cls)
            super().__init_subclass__()


class CheckPlain[T](CheckCls): ...


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
BaseModelWithNothing.__parameters__
CheckPlain.__type_params__
CheckPlain.__parameters__


def check_cls():
    # CheckBaseModel.__init_subclass__
    CheckBasicBaseModel.__type_params__
    CheckBaseModel.__class_getitem__(int)
    CheckBaseModel

    CheckBaseModel[int].check()
    CheckBaseModel2[int].check()
    CheckPlain[int].check()
    CheckAttrs[int].check()
    CheckTorch[int].check()

    CheckBaseModel[int]().check()
    CheckBaseModel2[int](field=1).check()
    CheckPlain[int]().check()
    CheckAttrs[int]().check()
    CheckTorch[int]().check()


def main():
    check_cls()


if __name__ == "__main__":
    main()
