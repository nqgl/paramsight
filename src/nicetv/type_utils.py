import typing
from collections.abc import Callable
from types import GenericAlias, get_original_bases
from typing import (
    Annotated,
    Any,
    Self,
    TypeGuard,
    TypeIs,
    TypeVar,
    get_args,
    get_origin,
)

from attrs import define, field
from pydantic import BaseModel

_NODEFAULT = object()


def get_args_robust(t: Any) -> tuple[Any, ...]:
    if _is_pydantic(t):
        if not hasattr(t, "__pydantic_generic_metadata__"):
            return ()
        return t.__pydantic_generic_metadata__["args"]
    return get_args(t)


def get_origin_robust(ga):
    if _is_pydantic(ga):
        if not hasattr(ga, "__pydantic_generic_metadata__"):
            return None
        return ga.__pydantic_generic_metadata__["origin"]
    return get_origin(ga)


def _is_typevar(x: Any) -> TypeGuard[TypeVar]:
    if isinstance(x, TypeVar):
        return True
    return getattr(x, "__class__", type(x)).__name__ == "TypeVar"


def _get_typevar_default(tv: Any) -> Any:
    return getattr(tv, "__default__", getattr(tv, "default", _NODEFAULT))


def _has_args_in_mro(cls: type) -> bool:
    if get_args_robust(cls):
        return True
    if any(get_args_robust(base) for base in get_original_bases(cls)):
        return True
    for cl in cls.mro():
        for base in get_original_bases(cl):
            origin = get_origin_robust(base)
            if origin is None or not isinstance(origin, type):
                continue
            if get_args_robust(base):
                return True
    return False


def chill_issubclass(
    cls: type | GenericAlias, target_type: type | GenericAlias
) -> bool:
    cls_t = cls if isinstance(cls, type) else get_origin_robust(cls)
    target_t = (
        target_type if isinstance(target_type, type) else get_origin_robust(target_type)
    )
    assert isinstance(cls_t, type)
    assert isinstance(target_t, type)
    if cls_t is target_t:
        if isinstance(target_type, type):
            return True
        if isinstance(cls, type):
            return False
        cls_t_params = typing.get_args_robust(cls)
        target_t_params = typing.get_args_robust(target_type)
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
        return unwrap_annotated(get_args_robust(param)[0])
    return param


def is_generic_alias(cls: type | GenericAlias) -> TypeGuard[GenericAlias]:
    if _is_pydantic(cls):
        if not hasattr(cls, "__pydantic_generic_metadata__"):
            return False
        return cls.__pydantic_generic_metadata__["origin"] is not None
    if isinstance(cls, typing._GenericAlias):  # type: ignore
        return True
    elif isinstance(cls, GenericAlias):
        return True
    return False


def _get_typevar_subst_edges_list(cls: type) -> list[list[tuple[int, int]]]:
    if is_generic_alias(cls):
        orig = get_origin(cls)
    else:
        orig = cls
    params = orig.__type_params__
    return [
        [
            (src_idx, tgt_idx)
            for src_idx, param in enumerate(params)
            for tgt_idx, t in enumerate(get_args_robust(b))
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
            arg = get_args_robust(self.root.ga)[self.root_typevar_idx]
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
        tv_edges = _get_typevar_subst_edges_list(typenode.cls)
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
            print(" " * indent, "chains_to:")
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
            return cls(ga=ga, orig=TypeNode.make(get_origin_robust(ga)))
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
                        f"duplicated paths found? result = {result},"
                        " keys = {res_d.keys()}"
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


def determine_type_arg(
    cls: type | GenericAlias, target_type: type, target_idx: int = 0
) -> type | GenericAlias | None:
    ga = GenericAliasNode.make(cls)
    return ga.find_type(target_type)[target_idx].resolve_to_value()


def main(): ...


if __name__ == "__main__":
    main()

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
