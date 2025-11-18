import importlib
import inspect
from types import FunctionType
from typing import Self

from pydantic import BaseModel

from paramsight import get_resolved_typevars_for_base


def get_src(obj):
    if obj.__module__ == "builtins":
        return "builtins"
    module = importlib.import_module(obj.__module__)
    return inspect.getsource(module)


class ObjRef(BaseModel):
    module: str
    cls_name: str
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
                f" same as the original: {test_obj} != {o}"
            )
        return cls(module=module, cls_name=name, source_backup=source_backup)

    def get_obj(self, strict: bool = False):
        module = importlib.import_module(self.module)
        obj = getattr(module, self.cls_name)
        if get_src(obj) != self.source_backup:
            print(
                """
                warning: loaded architecture source code appears to have changed since the model was saved. 
                This may cause issues.
                (but not necessarily)
                """
            )
            if strict:
                raise ValueError(
                    "loaded architecture source code has changed since this model was saved"
                )
        return obj

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(module={self.module}, cls_name={self.cls_name})"
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
