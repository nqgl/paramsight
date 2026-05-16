from attrs import define

from paramsight import get_args_at_base, takes_alias


class B:
    @takes_alias
    @classmethod
    def get_type_b(cls):
        return get_args_at_base(cls, B)


@define
class A[T](B):
    x: T

    @takes_alias
    @classmethod
    def get_type(cls):
        return get_args_at_base(cls, A)

    @takes_alias
    @classmethod
    def make_a(cls):
        return cls(x=1)

    @takes_alias
    @classmethod
    def make_a_with_type(cls, x: T):
        return cls(x=x)


class C[T]:
    @takes_alias
    @classmethod
    def get_type_c(cls):
        return get_args_at_base(cls, C)


@define
class D[T]:
    @takes_alias
    @classmethod
    def get_type_d(cls):
        return get_args_at_base(cls, D)


@define
class E[T](D[T]):
    @takes_alias
    @classmethod
    def get_type_e(cls):
        return get_args_at_base(cls, E)


def test_a_class_get_type():
    assert A[int].get_type() == (int,)


def test_a_instance_get_type():
    assert A[int](x=1).get_type() == (int,)


def test_a_make_a_get_type():
    assert A[int].make_a().get_type() == (int,)


def test_a_class_get_type_b():
    assert A[int].get_type_b() == ()


def test_a_instance_get_type_b():
    assert A[int](x=1).get_type_b() == ()


def test_a_make_a_get_type_b():
    assert A[int].make_a().get_type_b() == ()


def test_c_class_get_type_c():
    assert C[int].get_type_c() == (int,)


def test_c_instance_get_type_c():
    assert C[int]().get_type_c() == (int,)


def test_d_class_get_type_d():
    assert D[int].get_type_d() == (int,)


def test_d_instance_get_type_d():
    assert D[int]().get_type_d() == (int,)


def test_e_class_get_type_e():
    assert E[int].get_type_e() == (int,)


def test_e_instance_get_type_e():
    assert E[int]().get_type_e() == (int,)


def test_e_class_get_type_d():
    assert E[int].get_type_d() == (int,)


def test_e_instance_get_type_d():
    assert E[int]().get_type_d() == (int,)
