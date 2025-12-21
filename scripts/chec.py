def f1():
    def f():
        print(f)
        print(f.__name__)

    f.__name__ = "f!"
    return f


f = f1()
f()


class I:
    def __init_subclass__(cls):
        super().__init_subclass__()
        print("called __init_subclass__", cls)


class J(I): ...


# J.__init_subclass__()

import inspect

inspect.getattr_static(J, "__init_subclass__")


class K[T](I): ...


K.__type_params__
K.__parameters__


class B: ...


def mk_init_subclass(owner):
    def f(cls, *a, **kw):
        print(f"called f {owner.__name__}->{cls.__name__}")
        super(owner, cls).__init_subclass__(*a, **kw)

    return f


B.__init_subclass__ = classmethod(mk_init_subclass(B))


class C(B): ...


print()

B.__init_subclass__
C.__init_subclass__ = classmethod(mk_init_subclass(C))
inspect.getattr_static(B, "__init_subclass__") == inspect.getattr_static(
    C, "__init_subclass__"
)

inspect.getattr_static(B, "__init_subclass__")


class D(C): ...


class E(D):
    def __init_subclass__(cls, *a, **kw):
        print(f"calling the custom __init_subclass__ in E->{cls.__name__}")
        super().__init_subclass__(*a, **kw)


inspect.getattr_static(D, "__init_subclass__").__func__.__name__


class F(E): ...


F.__init_subclass__ = classmethod(mk_init_subclass(F))


class G(F): ...


G.__init_subclass__ = classmethod(mk_init_subclass(G))
