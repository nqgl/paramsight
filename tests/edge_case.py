from paramsight import get_resolved_typevars_for_base


class Cfg: ...


class ArchBase[CfgT: Cfg]: ...


class CfgBase[T](Cfg): ...


class SubCfg: ...


class BCfg[T](CfgBase[T]): ...


class ArchCfg: ...


class Arch[InnerCfgT](ArchBase[CfgBase[InnerCfgT]]): ...


class ArchImplCfg(Cfg): ...


class ArchImpl(Arch[ArchImplCfg]): ...


print(get_resolved_typevars_for_base(ArchImpl, ArchBase))
print(get_resolved_typevars_for_base(Arch[ArchImplCfg], ArchBase))
