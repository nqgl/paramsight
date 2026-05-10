"""Tests for resolving typevars when they appear nested inside a base's
generic arguments — e.g. ``class Arch[T](ArchBase[Wrap[T]])``.

The bug being guarded against: when a class's typevar appears wrapped inside
another generic alias in a base specification, the chain-edge mechanism
(matching ``t is param``) does not record an edge, and resolution previously
returned the unresolved alias (``Wrap[T]``) instead of substituting in the
concrete argument.
"""

from paramsight import get_resolved_typevars_for_base

# --- Building blocks reused by several tests ---------------------------------


class Cfg: ...


class ArchBase[CfgT: Cfg]: ...


class CfgBase[T](Cfg): ...


class Arch[InnerCfgT](ArchBase[CfgBase[InnerCfgT]]): ...


class ArchImplCfg(Cfg): ...


class ArchImpl(Arch[ArchImplCfg]): ...


# --- The original edge case --------------------------------------------------


def test_nested_typevar_via_subclass():
    """ArchImpl subclasses Arch[ArchImplCfg]; ArchBase.CfgT should resolve
    to CfgBase[ArchImplCfg], not the unresolved CfgBase[InnerCfgT]."""
    assert get_resolved_typevars_for_base(ArchImpl, ArchBase) == (CfgBase[ArchImplCfg],)


def test_nested_typevar_via_subscript():
    """Same resolution should hold when starting from the generic alias
    Arch[ArchImplCfg] directly rather than via a subclass."""
    assert get_resolved_typevars_for_base(Arch[ArchImplCfg], ArchBase) == (
        CfgBase[ArchImplCfg],
    )


def test_nested_typevar_intermediate_resolution_still_works():
    """The intermediate base Arch should still resolve to its own typevar."""
    assert get_resolved_typevars_for_base(ArchImpl, Arch) == (ArchImplCfg,)


# --- Two-level nested wrapping ----------------------------------------------


class Outer[T]: ...


class Pair[A, B]: ...


class TwoLevelArch[X](ArchBase[CfgBase[Outer[X]]]): ...


class TwoLevelImpl(TwoLevelArch[ArchImplCfg]): ...


def test_doubly_nested_typevar_via_subclass():
    assert get_resolved_typevars_for_base(TwoLevelImpl, ArchBase) == (
        CfgBase[Outer[ArchImplCfg]],
    )


def test_doubly_nested_typevar_via_subscript():
    assert get_resolved_typevars_for_base(TwoLevelArch[ArchImplCfg], ArchBase) == (
        CfgBase[Outer[ArchImplCfg]],
    )


# --- Multiple typevars: some nested, some direct ----------------------------


class MultiBase[A, B]: ...


class MultiArch[U, V](MultiBase[CfgBase[U], V]): ...


class MultiImpl(MultiArch[int, str]): ...


def test_mixed_nested_and_direct_typevars():
    assert get_resolved_typevars_for_base(MultiImpl, MultiBase) == (
        CfgBase[int],
        str,
    )


def test_mixed_nested_and_direct_typevars_subscript():
    assert get_resolved_typevars_for_base(MultiArch[int, str], MultiBase) == (
        CfgBase[int],
        str,
    )


# --- Three levels of inheritance with nesting at each step ------------------


class L1[A]: ...


class L2[B](L1[CfgBase[B]]): ...


class L3[C](L2[Outer[C]]): ...


class L3Impl(L3[int]): ...


def test_chained_nested_substitution():
    """Substitutions must compose across levels: L3[int] -> L2[Outer[int]]
    -> L1[CfgBase[Outer[int]]]."""
    assert get_resolved_typevars_for_base(L3Impl, L1) == (CfgBase[Outer[int]],)
    assert get_resolved_typevars_for_base(L3Impl, L2) == (Outer[int],)
    assert get_resolved_typevars_for_base(L3[int], L1) == (CfgBase[Outer[int]],)


# --- Reordering of typevars across nesting ----------------------------------


class Swap[X, Y](Pair[CfgBase[Y], Outer[X]]): ...


class SwapImpl(Swap[int, str]): ...


def test_typevars_swapped_inside_nested_args():
    assert get_resolved_typevars_for_base(SwapImpl, Pair) == (
        CfgBase[str],
        Outer[int],
    )


# --- A typevar appearing more than once in nested args ----------------------


class Dup[T](Pair[CfgBase[T], Outer[T]]): ...


class DupImpl(Dup[int]): ...


def test_repeated_typevar_in_nested_args():
    assert get_resolved_typevars_for_base(DupImpl, Pair) == (
        CfgBase[int],
        Outer[int],
    )


# --- Specializing with a generic alias instead of a concrete type -----------


class GenericArg[Q]: ...


def test_substitution_with_generic_alias_argument():
    """When the outer specialization itself supplies a generic alias as the
    argument, that alias should appear inside the resolved nested type."""
    assert get_resolved_typevars_for_base(Arch[GenericArg[int]], ArchBase) == (
        CfgBase[GenericArg[int]],
    )


# --- Sibling generic bases (only one of which is the target) ----------------


class SiblingA[T]: ...


class SiblingB[T]: ...


class WithSiblings[U](SiblingA[CfgBase[U]], SiblingB[U]): ...


class WithSiblingsImpl(WithSiblings[int]): ...


def test_sibling_bases_nested_resolves_correctly():
    assert get_resolved_typevars_for_base(WithSiblingsImpl, SiblingA) == (CfgBase[int],)


def test_sibling_bases_direct_still_resolves():
    assert get_resolved_typevars_for_base(WithSiblingsImpl, SiblingB) == (int,)
