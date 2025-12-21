from paramsight._paramsight import GenericAliasNode, TypeNode
from paramsight.type_utils import determine_type_arg


def main():
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

    # print(get_type_arg(B[CfgC]))  # CfgC
    # print(get_type_arg(B))  # CfgB  (default)
    # cls = B[CfgC]
    # print(get_parents_typevar_substitutes_to(cls))
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
