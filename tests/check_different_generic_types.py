import torch.nn as nn
from pydantic import BaseModel
from attrs import define
from nicetv.mapytype import aliasclassmethod


class CheckCls:
    @aliasclassmethod
    def check(cls):
        assert type(cls) is not type

    def __init_subclass__(cls) -> None:
        print("called __init_subclass__", cls)
        super().__init_subclass__()


class CheckBaseModel[T](BaseModel, CheckCls): ...


class CheckBaseModel2[T](BaseModel, CheckCls):
    field: T


@define
class CheckAttrs[T](CheckCls):
    ...
    # field: T


class CheckPlain[T](CheckCls): ...


class CheckTorch[T](nn.Module, CheckCls): ...


def check_cls():
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
