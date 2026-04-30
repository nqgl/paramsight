import importlib
import inspect
from types import FunctionType
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, Field

from paramsight import get_resolved_typevars_for_base, takes_alias
from paramsight.type_utils import get_origin_robust


def get_src(obj):
    if obj.__module__ == "builtins":
        return "builtins"
    module = importlib.import_module(obj.__module__)
    return inspect.getsource(module)


class ObjRef[T = object](BaseModel):
    module: str
    obj_name: str = Field(validation_alias=AliasChoices("cls_name", "obj_name"))
    source_backup: str | None = None

    @classmethod
    def from_obj(cls, o: FunctionType | type, dynamic_okay: bool = False) -> Self:
        module = o.__module__
        if module == "__main__":
            raise ValueError(f"cannot safe ref to {o}: module is __main__")
        name = o.__name__
        source_backup = get_src(o)
        test_module = importlib.import_module(module)
        test_obj = getattr(test_module, name)
        if test_obj is not o and not dynamic_okay:
            raise ValueError(
                f"cannot safe ref to {o}: test-imported object is not"
                f" the same object as the original: {test_obj} is not {o}"
                + ("\nhowever, they are equal" if test_obj == o else "")
                + "\nthis can be ignored by passing"
                + " dynamic_okay=True to ObjRef.from_obj()"
            )
        return cls(module=module, obj_name=name, source_backup=source_backup)

    @takes_alias
    @classmethod
    def get_t(cls) -> type[T]:
        (t,) = get_resolved_typevars_for_base(cls, ObjRef)
        return t  # type: ignore

    def get_obj(self, strict: bool = False) -> T:
        module = importlib.import_module(self.module)
        obj = getattr(module, self.obj_name)
        if get_src(obj) != self.source_backup:
            print(
                f"""
                warning: loaded source code for {self.obj_name} appears to have changed 
                """
            )
            if strict:
                raise ValueError(
                    "loaded object source code has changed since this model was saved"
                )
        t = self.get_t()

        if get_origin_robust(t) is type:
            import typing

            # type_t = get_resolved_typevars_for_base(t, type)
            (type_t,) = typing.get_args(t)
            valid = issubclass(obj, type_t)
        else:
            valid = isinstance(obj, t)
        if not valid:
            raise ValueError(f"loaded object is not of type {self.get_t()}: {obj}")
        return obj

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(module={self.module}, cls_name={self.obj_name})"
        )


class TypeRef(BaseModel):
    base: ObjRef
    params: "tuple[TypeRef, ...] | None" = None

    @classmethod
    def from_ga(cls, ga):
        from paramsight.type_utils import get_args_robust, get_origin_robust

        base = get_origin_robust(ga)
        args = get_args_robust(ga)
        assert (base is None) == (len(args) == 0)
        if base is None:
            assert isinstance(ga, type)
            return cls(base=ObjRef.from_obj(ga), params=None)
        arg_values = get_resolved_typevars_for_base(ga, base)
        return cls(
            base=ObjRef.from_obj(base),
            params=tuple(cls.from_ga(arg_value) for arg_value in arg_values),
        )

    def get(self):
        if self.params is None:
            return self.base.get_obj()
        params = tuple(param.get() for param in self.params)
        return self.base.get_obj()[*params]
