import typing
from types import GenericAlias, get_original_bases
from typing import (
    TYPE_CHECKING,
    Annotated,
    Callable,
    Concatenate,
    Mapping,
    ParamSpec,
    Self,
    get_args,
    get_origin,
    Any,
    TypeGuard,
    TypeVar,
    TypeIs,
    NoDefault,
    overload,
)
from typing_extensions import get_original_bases

from pydantic import BaseModel, Field

from attrs import define, field

# import typing._GenericAlias

_NODEFAULT = object()


def _is_typevar(x: Any) -> TypeGuard[TypeVar]:
    # Works across typing/typing_extensions and Python versions
    if isinstance(x, TypeVar):
        return True
    return getattr(x, "__class__", type(x)).__name__ == "TypeVar"


def _get_typevar_default(tv: Any) -> Any:
    # PEP 696 default may live on __default__ (py3.12/te), sometimes "default"
    return getattr(tv, "__default__", getattr(tv, "default", _NODEFAULT))


def _has_args_in_mro(cls: type) -> bool:
    if get_args(cls):
        return True
    if any(get_args(base) for base in get_original_bases(cls)):
        return True
    for cl in cls.mro():
        for base in get_original_bases(cl):
            origin = get_origin(base)
            if origin is None or not isinstance(origin, type):
                continue
            if get_args(base):
                return True
    return False


def chill_issubclass(
    cls: type | GenericAlias, target_type: type | GenericAlias
) -> bool:
    cls_t = cls if isinstance(cls, type) else typing.get_origin(cls)
    target_t = (
        target_type if isinstance(target_type, type) else typing.get_origin(target_type)
    )
    assert isinstance(cls_t, type)
    assert isinstance(target_t, type)
    if cls_t is target_t:
        if isinstance(target_type, type):
            return True
        if isinstance(cls, type):
            return False
        cls_t_params = typing.get_args(cls)
        target_t_params = typing.get_args(target_type)
        assert len(cls_t_params) == len(target_t_params)
        results = []
        for cls_t_param, target_t_param in zip(
            cls_t_params, target_t_params, strict=True
        ):
            results.append(chill_issubclass(cls_t_param, target_t_param))
        if any(results):
            assert all(results)
            return True
        return False
    return issubclass(cls_t, target_t)


def unwrap_annotated(param: Any) -> type:
    inner = get_origin(param)
    if inner is Annotated:
        return unwrap_annotated(get_args(param)[0])
    return param


def get_max_mro_cls(
    cls: type,
    target_type: type,
    ident: int = 0,
    allowed_origins: tuple[type, ...] | None = None,
) -> type:
    """
    Scan `cls`'s MRO for bases like ArchitectureBase[Cfg] / WithConfig[Cfg],
    collect the config type arguments, and return the most specific subclass
    of `target_type`. If none found, return `target_type`.
    """
    params: list[type] = []

    for cl in cls.mro():
        for base in get_original_bases(cl):
            origin = get_origin(base)
            if origin is None or not isinstance(origin, type):
                print(
                    f"origin is None or not isinstance(origin, type) {origin}. skipping"
                )
                continue

            # Only consider bases that are subclasses of the allowed origins
            if allowed_origins and not any(
                chill_issubclass(origin, a) for a in allowed_origins
            ):
                continue

            args = list(get_args(base))
            if not args:
                continue
            for param in args:
                # Unwrap Annotated[T, ...] -> T
                param = unwrap_annotated(param)

                # Resolve TypeVar default, if any
                if _is_typevar(param):
                    default = _get_typevar_default(param)

                    if default is _NODEFAULT or default is NoDefault:
                        # No default provided; skip this base.
                        continue
                    print(f"default found {param} -> {default}")
                    param = default
                    if _has_args_in_mro(param):
                        print(f"param {param} has args in mro")
                        max_cls = get_max_mro_cls(param, target_type, ident=ident + 1)
                        if max_cls is not None:
                            print(f"max_cls {max_cls} found in {param}")
                            params.append(max_cls)
                        else:
                            print(f"max_cls is None for {param}")

                # Ignore Any / non-classes
                if param is Any:
                    continue

                if not isinstance(param, type):
                    print(f"param {param} is not a type")
                    param_origin = get_origin(param)
                    if param_origin is not None:
                        print(f"param_origin {param_origin}")
                    else:
                        print(f"param_origin is None for {param}")
                    assert isinstance(param_origin, type)
                    if not issubclass(param_origin, target_type):
                        print(
                            f"param_origin {param_origin} is not a subclass of {target_type}, continuing"
                        )
                        continue

                # Keep only subclasses of the requested target_type
                elif not issubclass(param, target_type):
                    continue

                if param not in params:
                    params.append(param)

    # Nothing matched -> default to target_type
    if not params:
        return None

    # Ensure a linear subclass chain and pick the most specific
    most_specific: type = target_type
    for p in params:
        if most_specific is target_type or chill_issubclass(p, most_specific):
            most_specific = p
        elif chill_issubclass(most_specific, p):
            pass
        else:
            raise ValueError(
                f"Architecture config classes must be able to be resolved into "
                f"a sequence of subclasses; "
                f"found unrelated types: {most_specific} and {p}"
            )
    print(
        f"{'    ' * ident}"
        f"most_specific {most_specific} found when scanning {cls} for {target_type}"
    )
    return most_specific

    # def get_target_class_generic_param(cls: type|GenericAlias, target_type: type) -> tuple[type]:


def check_mrobase(cls: type, prefix: str = ""):
    SEP = " -> "
    for cl in cls.mro():
        print(prefix, f"{cls}{SEP}{cl}")
        for base in get_original_bases(cl):
            origin = get_origin(base)
            print(prefix, f"{cls}{SEP}{cl}{SEP}{base}{SEP}{origin}")
            if origin is None or not isinstance(origin, type):
                if origin is None:
                    continue
                print(
                    prefix,
                    f"origin is not isinstance(origin, type): {origin!r}. skipping",
                )
                continue

            # # Only consider bases that are subclasses of the allowed origins
            # if allowed_origins and not any(
            #     chill_issubclass(origin, a) for a in allowed_origins
            # ):
            # continue

            args = list(get_args(base))
            for param in args:
                print(prefix, f"{cls}{SEP}{cl}{SEP}{base}{SEP}{origin}{SEP}{param}")
                if _is_typevar(param):
                    print(prefix, f"param {param} is a typevar")
                    default = _get_typevar_default(param)
                    if default is _NODEFAULT or default is NoDefault:
                        print(prefix, f"param {param} has no default")
                        continue
                    print(prefix, f"param {param} has default {default}")
                    param = default
                    if _has_args_in_mro(param):
                        print(prefix, f"param {param} has args in mro")
                        check_mrobase(
                            param,
                            prefix=prefix
                            + f"{cls}{SEP}{cl}{SEP}{base}{SEP}{origin}{SEP}{param}{SEP}",
                        )
                        # max_cls = get_max_mro_cls(param, target_type, ident=ident + 1)
                        # if max_cls is not None:
                        #     print(prefix, f"max_cls {max_cls} found in {param}")
                        # params.append(max_cls)


# class Cfg(BaseModel):
#     x: int


# class A[T: Cfg](BaseModel):
#     cfg: T


# class CfgB(Cfg):
#     y: int


# class B[T: CfgB = CfgB](A[T]): ...


# class CfgC(CfgB):
#     z: str


def get_type_arg(cls, i: int = 0):
    args = get_args(cls)
    if args:  # e.g. B[CfgC]
        return args[i]
    # Unspecialized: try PEP 695 type params with defaults
    params = getattr(cls, "__type_params__", ())
    if params and getattr(params[i], "default", None) is not None:
        return params[i].default  # e.g. CfgB, the default for B[T=CfgB]
    return None


def is_generic_alias(cls: type | GenericAlias) -> TypeGuard[GenericAlias]:
    if isinstance(cls, typing._GenericAlias):  # type: ignore
        return True
    elif isinstance(cls, GenericAlias):
        return True
    return False


def get_parents_typevar_substitutes_to(cls: type, idx: int = 0) -> list[type]:
    """
    Scan `cls`'s MRO for bases like ArchitectureBase[Cfg] / WithConfig[Cfg],
    collect the config type arguments, and return the most specific subclass
    of `target_type`. If none found, return `target_type`.
    """
    if is_generic_alias(cls):
        orig = get_origin(cls)
    else:
        orig = cls
    params = orig.__type_params__
    param = params[idx]
    # isinstance(cls, typing._GenericAlias)
    # isinstance(orig, typing._GenericAlias)

    # cls.__type_params__
    # orig.__type_params__[0].__value__
    # get_args(orig)
    assert param is not None
    l = []
    for b in get_original_bases(orig):
        print(f"b {b}")
        for t in get_args(b):
            print(f"t {t}")
            if t is param:
                l.append(b)

    return l

    for cl in cls.mro():
        # for base in get_original_bases(cl):
        print(f"cl {cl}")
        for t in get_args(cl):
            print(f"t {t}")
            if t is param:
                l.append(cl)

    return l


def get_typevar_subst_edges_list(cls: type) -> list[list[tuple[int, int]]]:
    """
    Scan `cls`'s MRO for bases like ArchitectureBase[Cfg] / WithConfig[Cfg],
    collect the config type arguments, and return the most specific subclass
    of `target_type`. If none found, return `target_type`.
    """
    if is_generic_alias(cls):
        orig = get_origin(cls)
    else:
        orig = cls
    params = orig.__type_params__
    return [
        [
            (src_idx, tgt_idx)
            for src_idx, param in enumerate(params)
            for tgt_idx, t in enumerate(get_args(b))
            if t is param
        ]
        for b in get_original_bases(orig)
    ]


def _make_type_guard[T](t: type[T]) -> Callable[[Any], TypeGuard[T]]:
    def guard(obj: Any) -> TypeGuard[T]:
        return isinstance(obj, t)

    return guard


def _make_typeis_guard[T](t: type[T]) -> Callable[[Any], TypeIs[T]]:
    def guard(obj: Any) -> TypeIs[T]:
        return isinstance(obj, t)

    return guard


def _assert_is_instance[T](obj: Any, cls: type[T]) -> T:
    assert _make_typeis_guard(cls)(obj)
    return obj


def _make_issubclass_guard[T](t: type[T]) -> Callable[[Any], TypeGuard[type[T]]]:
    def guard(obj: Any) -> TypeGuard[type[T]]:
        return issubclass(obj, t)

    return guard


@define
class TypeVarTracePath:
    root: "GenericAliasNode"
    root_typevar_idx: int
    typevar_chain_path: tuple[int, ...] = field(factory=tuple)

    def add_link(self, link_idx: int) -> Self:
        return self.__class__(
            root=self.root,
            root_typevar_idx=self.root_typevar_idx,
            typevar_chain_path=self.typevar_chain_path + (link_idx,),
        )

    def get_typevar_sequence(self) -> list["TypeVarNode"]:
        node = self.root.orig.typevars[self.root_typevar_idx]
        nodes = [node]
        for i in self.typevar_chain_path:
            node = node.chains_to[i]
            nodes.append(node)
        return nodes

    def resolve_to_value(
        self, return_bound_as_fallback: bool = False
    ) -> type | GenericAlias | None:
        if self.root.ga:
            arg = get_args(self.root.ga)[self.root_typevar_idx]
            if arg is not None:
                if _is_typevar(arg):
                    return _get_typevar_default(arg)
                return arg
        nodes = self.get_typevar_sequence()
        node = nodes[0]
        if node.default:
            return node.default
        if return_bound_as_fallback:
            for node in nodes:
                if node.bound:
                    return node.bound
        return None


@define
class TypeVarNode:
    typevar: TypeVar
    default: type | None
    home: "TypeNode"
    home_idx: int
    chains_to: list["TypeVarNode"]
    bound: type | GenericAlias | None

    @classmethod
    def make_nodes(cls, typenode: "TypeNode") -> list[Self]:
        assert not typenode.typevars
        tv_edges = get_typevar_subst_edges_list(typenode.cls)
        type_params: list[TypeVar] = [
            _assert_is_instance(tv, TypeVar) for tv in typenode.cls.__type_params__
        ]

        return [
            cls(
                typevar=tv,
                default=_get_typevar_default(tv),
                home=typenode,
                home_idx=i,
                chains_to=[
                    base.orig.typevars[tv_edge_dst]
                    for base, base_tv_edges in zip(
                        typenode.bases, tv_edges, strict=True
                    )
                    for tv_edge_src, tv_edge_dst in base_tv_edges
                    if tv_edge_src == i and base.orig.cls is not typing.Generic
                ],
                bound=tv.__bound__,
            )
            for i, tv in enumerate(type_params)
        ]

    def pretty_print(self, indent: int = 0):
        print(" " * indent, f"{self.typevar}")
        print(" " * indent, f"default: {self.default}")
        print(" " * indent, f"home: {self.home}")
        if self.chains_to:
            print(" " * indent, f"chains_to:")
            for target in self.chains_to:
                target.pretty_print(indent + 2)

    def __repr__(self):
        boundstr = f": {self.bound!r}" if self.bound else ""
        defaultstr = f" = {self.default!r}" if self.default else ""
        return f"TypeVarNode<{self.typevar!r}{boundstr}{defaultstr}>"

    def find_type(
        self, target_type: type, path: TypeVarTracePath
    ) -> dict[int, TypeVarTracePath]:
        if self.home.cls is target_type:
            return {self.home_idx: path}
        found = {}
        for i, tgt in enumerate(self.chains_to):
            result = tgt.find_type(target_type, path.add_link(i))
            if result.keys() & found.keys():
                raise ValueError(
                    f"duplicated paths found? result = {result}, found = {found}"
                )
            found.update(result)
        return found


@define
class TypeNode:
    cls: type
    bases: list["GenericAliasNode"]
    # ga_bases: list["GenericAliasNode | TypeNode"]
    typevars: list[TypeVarNode]

    @classmethod
    def make(cls, t: type) -> Self:
        bases = [GenericAliasNode.make(b) for b in get_original_bases(t)]
        inst = cls(cls=t, bases=bases, typevars=[])
        tvs = TypeVarNode.make_nodes(inst)
        inst.typevars.extend(tvs)
        # tv_edges = get_typevar_subst_edges_list(t)
        # for (tv_edge_src, tv_edge_dst), base in zip(tv_edges, bases, strict=True):
        #     src_tv = inst.typevars[tv_edge_src]
        #     dst_tv = base.typevars[tv_edge_dst]
        #     src_tv.chains_to.append(dst_tv)

        return inst

    def pretty_print(self, indent: int = 0):
        print(" " * indent, f"{self.cls}")
        for tv in self.typevars:
            tv.pretty_print(indent + 2)
        for base in self.bases:
            base.pretty_print(indent + 2)

    def __repr__(self):
        return f"TypeNode(cls={self.cls!r})"

    # def find_type(self, target_type: type) -> dict[int, TypeVarTracePath]:
    #     found = {}
    #     for i, tv in enumerate(self.typevars):
    #         result = tv.find_type(
    #             target_type,
    #             path=TypeVarTracePath(
    #                 root=self,
    #                 root_typevar_idx=i,
    #             ),
    #         )
    #         if result.keys() & found.keys():
    #             raise ValueError(
    #                 f"duplicated paths found? result = {result}, found = {found}"
    #             )
    #         found.update(result)
    #     return found


def get_num_typevars(cls: type | GenericAlias) -> int:
    if is_generic_alias(cls):
        cls = get_origin(cls)
    return len(cls.__type_params__)


@define
class GenericAliasNode:
    ga: GenericAlias | None
    orig: TypeNode

    @classmethod
    def make(cls, ga: GenericAlias | type) -> Self:
        if is_generic_alias(ga):
            return cls(ga=ga, orig=TypeNode.make(get_origin(ga)))
        else:
            return cls(ga=None, orig=TypeNode.make(ga))

    def pretty_print(self, indent: int = 0):
        print(" " * indent, f"{self.ga}")
        self.orig.pretty_print(indent + 2)

    def __repr__(self):
        if self.ga:
            return f"GenericAliasNode(ga={self.ga})"
        else:
            return f"GenericAliasNode(orig={self.orig.cls})"

    def find_type(
        self, target_base: type, found: dict[int, TypeVarTracePath] | None = None
    ) -> dict[int, TypeVarTracePath]:
        found = found or {}
        num_tv_in_tgt = get_num_typevars(target_base)

        # found: dict[int, TypeVarTracePath] = {}
        for i, tv in enumerate(self.orig.typevars):
            result = tv.find_type(
                target_base,
                path=TypeVarTracePath(
                    root=self,
                    root_typevar_idx=i,
                ),
            )
            if result.keys() & found.keys():
                raise ValueError(
                    f"duplicated paths found? result = {result}, found = {found}"
                )
            found.update(result)

        if len(found) < num_tv_in_tgt:
            res_d: dict[int, TypeVarTracePath] = {}
            for base in self.orig.bases:
                result = base.find_type(target_base)
                rk = result.keys() - found.keys()
                if rk & res_d.keys():
                    raise ValueError(
                        f"duplicated paths found? result = {result}, keys = {keys}"
                    )
                for k in rk:
                    res_d[k] = result[k]

            assert not res_d.keys() & found.keys()
            found.update(res_d)
        return found

    def get_resolved_typevars_for_base(
        self, target_base: type, return_bound_as_fallback: bool = False
    ) -> tuple[type | GenericAlias | None, ...]:
        num_tv_in_tgt = get_num_typevars(target_base)
        search = self.find_type(target_base)
        if len(search) != num_tv_in_tgt:
            raise ValueError(
                f"failed to locate all typevars for base {target_base}.\n"
                f"found {len(search)} typevars, expected {num_tv_in_tgt}\n"
                f"indices found: {search.keys()}"
            )
        return tuple(
            search[i].resolve_to_value(
                return_bound_as_fallback=return_bound_as_fallback
            )
            for i in range(num_tv_in_tgt)
        )


def get_resolved_typevars_for_base(
    cls: type | GenericAlias, target_base: type, return_bound_as_fallback: bool = False
) -> tuple[type | GenericAlias | None, ...]:
    ga = GenericAliasNode.make(cls)
    return ga.get_resolved_typevars_for_base(target_base, return_bound_as_fallback)


def get_resolved_typevars_for_base_inst(
    inst: Any, target_base: type, return_bound_as_fallback: bool = False
) -> tuple[type | GenericAlias | None, ...]:
    if hasattr(inst, "__orig_class__"):
        cls = inst.__orig_class__
    else:
        cls = inst.__class__
    return get_resolved_typevars_for_base(cls, target_base, return_bound_as_fallback)


def main():
    check_cls()
    # cls = B[CfgC]
    # check_mrobase(cls)
    # print("-" * 100)
    # print(get_args(cls))
    # print(get_origin(cls))
    # print(get_args(get_original_bases(cls)[0]))
    # print(get_max_mro_cls(cls, Cfg))
    # print(cls.model_fields["cfg"].annotation)
    other()


def determine_type_arg(
    cls: type | GenericAlias, target_type: type, target_idx: int = 0
) -> type | GenericAlias | None:
    ga = GenericAliasNode.make(cls)
    return ga.find_type(target_type)[target_idx].resolve_to_value()


from typing import Protocol


class _ClassMethodCallable[T, **P, R_co](Protocol):
    def __call__(self, __cls: type[T], *args: P.args, **kwargs: P.kwargs) -> R_co: ...


class origclassmethod[T, **P, R_co](classmethod):
    # @overload
    # def __init__(self, f: Callable[Concatenate[type[T], P], R_co], /) -> None: ...
    # @overload
    # def __init__(self, f: _ClassMethodCallable[T, P, R_co], /) -> None: ...
    # def __init__(self, f: Any, /) -> None:
    #     super().__init__(f)

    @overload
    def __get__(
        self, instance: T, owner: type[T] | None = None, /
    ) -> Callable[P, R_co]: ...
    @overload
    def __get__(self, instance: None, owner: type[T], /) -> Callable[P, R_co]: ...
    def __get__(
        self, instance: T | None, owner: type[T] | None = None, /
    ) -> Callable[P, R_co]:
        if instance is None:
            assert owner is not None
            r = super().__get__(instance, owner)
            return r
        if hasattr(instance, "__orig_class__"):
            return super().__get__(None, instance.__orig_class__)
        return super().__get__(instance, owner)


# if TYPE_CHECKING:

#     class origclassmethod(classmethod): ...

#     # origclassmethod = classmethod  # noqa: F811

# origclassmethod = _origclassmethod  # noqa: F811


class C[T: Mapping = dict[int, int]]:
    def __init__(self, value: T):
        self.value = value

    def get_typevars(self, base_tgt: type):
        return get_resolved_typevars_for_base(self.__class__, base_tgt)

    @origclassmethod
    def get_typevars_cls(cls, base_tgt: type):
        return get_resolved_typevars_for_base(cls, base_tgt)

    @classmethod
    def check_class(cls):
        print(f"cls {cls}")


class CheckCls:
    @classmethod
    def check(cls):
        print(f"cls {cls}")
        print(f"type of cls {type(cls)}")


class CheckBaseModel[T](BaseModel, CheckCls): ...


class CheckBaseModel2[T](BaseModel, CheckCls):
    field: T


@define
class CheckAttrs[T](CheckCls):
    field: T


class CheckPlain[T](CheckCls): ...


import torch.nn as nn


class CheckTorch[T](nn.Module, CheckCls): ...


def check_cls():
    CheckBaseModel[int].check()
    CheckBaseModel2[int].check()
    CheckAttrs[int].check()
    CheckPlain[int].check()
    CheckTorch[int].check()


def other():
    class Cfg:
        x: int

    class A[T: Cfg]:
        cfg: T

    class CfgB(Cfg):
        y: int

    class B[GT: CfgB = CfgB, CT: float = float](A[GT]): ...

    class CfgC(CfgB):
        z: str

    class C[CT: float = int, TT: CfgB = CfgB](B[TT, CT]): ...

    class D[T0, T1, CT: CfgB = CfgC, T2 = bool](C[float, CT]): ...

    print(get_type_arg(B[CfgC]))  # CfgC
    print(get_type_arg(B))  # CfgB  (default)
    print(get_max_mro_cls(B[CfgC], Cfg))
    cls = B[CfgC]
    print(get_parents_typevar_substitutes_to(cls))
    # print(get_parents_typevar_substitutes_to(D[CfgC]))

    tn = TypeNode.make(B)
    tn = TypeNode.make(D)
    # tn.find_type(B)
    # tn.typevars[0].chains_to[0].home.cls
    print(determine_type_arg(D, B))
    ga = GenericAliasNode.make(D)
    search = ga.find_type(B)
    print(search[0].resolve_to_value(), search[1].resolve_to_value())
    # print(tn.typevars)
    print(tn)
    tn.pretty_print()


if __name__ == "__main__":
    main()
