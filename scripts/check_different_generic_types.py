from typing import Any, Protocol

import torch.nn as nn
from attrs import define
from pydantic import BaseModel

from nicetv.aliasclassmethod import (
    _is_specialized_generic,
    takes_alias,
)
from scripts.checkme import checkme


class Variations:
    has_init_subclass: bool = True


class ClassMethod(Protocol):
    @classmethod
    def __call__(cls: type[Any], *args: Any, **kwargs: Any) -> Any: ...


class CheckCls:
    @takes_alias
    @classmethod
    def check(cls):
        print("checked", cls, type(cls))
        # issubclass(cls, _GAProxy)
        # stack = inspect.stack()
        # print(inspect.stack())
        # extract_generic_args_from_stack(cls.__origin__)
        # find_generic_alias_in_stack(cls.__origin__)
        # 2
        # cls
        # t = inspect.currentframe().f_back.f_back
        # t.f_locals
        # stack[1].positions
        # stack[1].frame.f_locals["cls"]
        # stack[0].frame.f_code.co_name
        # stack[2].frame.f_code.co_name

        assert _is_specialized_generic(cls)
        cls.check_2(2)

    @takes_alias
    @classmethod
    def check_2(cls, arg: int):
        """
        checking that chained aliasclassmethod decorators work
        """
        print("checked 2", cls)
        assert _is_specialized_generic(cls)

    @takes_alias
    @classmethod
    def check_non_generic(cls):
        """
        check that non-generic passes correctly
        """
        print("checked non generic", cls)
        _is_specialized_generic(cls)
        assert type(cls) in (type, type(BaseModel))

    @classmethod
    def normal_classmethod(cls):
        print("normal classmethod", cls)
        assert not _is_specialized_generic(cls)

    @classmethod
    def normal_classmethod2(cls, arg: int):
        print("normal classmethod", cls)

    if Variations.has_init_subclass:

        def __init_subclass__(cls) -> None:
            print("called __init_subclass__", cls)
            super().__init_subclass__()


class CheckPlain[T](CheckCls): ...


# checkme = inject_locals(abcd=1234)(checkme)
checkme()
closedval = 2


class CheckPlainSuper[T](CheckCls):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        # type(cls)
        # stack = inspect.stack()
        # print(inspect.stack())
        # stack[1].frame.f_code.co_name
        # # stack[0].frame.f_code.
        # super().check()
        print("closedval", closedval)
        print(CheckPlainSuper)
        # s = super(
        super().normal_classmethod()
        return super().check()
        # return super(CheckPlainSuper, cls.__origin__).check.__func__(cls)
        # [int].check()
        # s.
        # return s.check()


class CheckPlainSuperDuper[T1](CheckPlainSuper[T1]):
    @takes_alias
    @classmethod
    def check(cls, arg=2):
        print("checking super duper", CheckPlainSuperDuper, CheckPlainSuper, cls)
        return super().check()


# CheckPlain[int]._gaproxy_alias


class CheckPlain2[T1, T2](CheckPlain[T2]): ...


class CheckBasicBaseModel[T](BaseModel):
    @takes_alias
    @classmethod
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

    type(CheckPlain2[int, str])
    # issubclass(CheckPlain[int], typing._GenericAlias)
    CheckPlain[int].check()
    CheckPlain2[int, float].check()
    CheckBaseModel[int].check()
    CheckBaseModel2[int].check()
    CheckPlainSuper[float]
    CheckPlainSuperDuper[float]
    CheckPlainSuper[int].check()
    CheckPlainSuperDuper[int].check()
    CheckAttrs[int].check()
    CheckTorch[int].check()

    CheckBaseModel[int]().check()
    CheckBaseModel2[int](field=1).check()
    CheckPlain[int]().check()
    CheckPlainSuper[int]().check()
    CheckPlainSuperDuper[int]().check()
    CheckAttrs[int]().check()
    CheckPlain2[int, float]().check()
    CheckTorch[int]().check()

    CheckPlain2.check_non_generic()
    CheckBaseModel.check_non_generic()
    CheckBaseModel2.check_non_generic()
    CheckPlain.check_non_generic()
    CheckPlainSuper.check_non_generic()
    CheckPlainSuperDuper.check_non_generic()
    CheckAttrs.check_non_generic()
    CheckTorch.check_non_generic()
    CheckPlain2.check_non_generic()
    CheckBaseModel.check_non_generic()
    CheckBaseModel2(field=1).check_non_generic()
    CheckPlain.check_non_generic()
    CheckPlainSuper.check_non_generic()
    CheckPlainSuperDuper.check_non_generic()
    CheckAttrs.check_non_generic()
    CheckTorch.check_non_generic()

    CheckPlain[int].normal_classmethod()
    CheckBaseModel.__pydantic_generic_metadata__
    CheckBaseModel[int].__parameters__


def main():
    check_cls()


if __name__ == "__main__":
    main()
