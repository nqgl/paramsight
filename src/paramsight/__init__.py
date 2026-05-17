import paramsight.slotted_strategies as slotted_strategies
from paramsight._paramsight import (
    get_args_at_base,
    get_resolved_typevars_for_base,
    get_typevar_value,
)
from paramsight.aliasclassmethod import takes_alias
from paramsight.typevar_value import TypeVarValue

# The slotted-class opt-ins (add_field / uses_side_table / uses_class_swap)
# live under the ``paramsight.slotted_strategies`` namespace rather than the
# top level.
__all__ = [
    "takes_alias",
    "get_resolved_typevars_for_base",
    "get_args_at_base",
    "get_typevar_value",
    "TypeVarValue",
    "slotted_strategies",
]
