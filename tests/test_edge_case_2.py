from typing import Generic, TypeVar

from paramsight import get_args_at_base


class Cfg: ...


class ArchBase[CfgT: Cfg]: ...


class CfgBase[T](Cfg): ...


class ArchImplCfg(Cfg): ...


class OtherArchImplCfg(Cfg): ...


class Arch[InnerCfgT](ArchBase[CfgBase[InnerCfgT]]): ...


class ArchImpl(Arch[ArchImplCfg]): ...


class OtherArchImpl(Arch[OtherArchImplCfg]): ...


class DefaultArch[InnerCfgT = ArchImplCfg](ArchBase[CfgBase[InnerCfgT]]): ...


class DefaultArchImpl(DefaultArch): ...


OldInnerCfgT = TypeVar("OldInnerCfgT")


class OldStyleArch(
    ArchBase[CfgBase[OldInnerCfgT]],
    Generic[OldInnerCfgT],  # noqa: UP046
): ...


class OldStyleArchImpl(OldStyleArch[ArchImplCfg]): ...


def test_nested_generic_arg_resolves_from_specialized_alias():
    assert get_args_at_base(Arch[ArchImplCfg], ArchBase) == (
        CfgBase[ArchImplCfg],
    )


def test_nested_generic_arg_resolves_through_concrete_subclass():
    assert get_args_at_base(ArchImpl, ArchBase) == (CfgBase[ArchImplCfg],)


def test_nested_generic_arg_uses_each_concrete_subclass_specialization():
    assert get_args_at_base(OtherArchImpl, ArchBase) == (
        CfgBase[OtherArchImplCfg],
    )


def test_nested_generic_arg_resolves_unspecialized_default():
    assert get_args_at_base(DefaultArchImpl, ArchBase) == (
        CfgBase[ArchImplCfg],
    )


def test_nested_generic_arg_resolves_old_style_generic_subclass():
    assert get_args_at_base(OldStyleArchImpl, ArchBase) == (
        CfgBase[ArchImplCfg],
    )


if __name__ == "__main__":
    test_nested_generic_arg_resolves_from_specialized_alias()
    # test_nested_generic_arg_resolves_through_concrete_subclass()
    # test_nested_generic_arg_uses_each_concrete_subclass_specialization()
    # test_nested_generic_arg_resolves_unspecialized_default()
    # test_nested_generic_arg_resolves_old_style_generic_subclass()
