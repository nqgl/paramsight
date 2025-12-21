from attrs import define
from paramsight import takes_alias, get_resolved_typevars_for_base


class B:
    @takes_alias
    @classmethod
    def get_type_b(cls):
        return get_resolved_typevars_for_base(cls, B)


@define
class A[T](B):
    x: T

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_resolved_typevars_for_base(cls, A)

    @takes_alias
    @classmethod
    def make_a(cls):
        return cls(x=1)

    @takes_alias
    @classmethod
    def make_a_with_type(cls, x: T):
        return cls(x=x)


print(A[int].get_type())
print(A[int](x=1).get_type())
print(A[int].make_a().get_type())
print(A[int].get_type_b())
print(A[int](x=1).get_type_b())
print(A[int].make_a().get_type_b())
