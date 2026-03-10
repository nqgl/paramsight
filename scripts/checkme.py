from functools import wraps

from paramsight.aliasclassmethod import inject_locals


def dec(n):
    def decin(fn):
        if hasattr(fn, "_dece"):
            print("wrappingdece:", fn._dece)

        @wraps(fn)
        def wrapped(*a, **k):
            print(f"dec{n}", fn)
            if hasattr(fn, "_dece"):
                print("dece:", fn._dece)
            return fn(*a, **k)

        return wrapped

    return decin


def dece(n):
    def mkdece(fn):
        if not hasattr(fn, "_dece"):
            fn._dece = []
        fn._dece.append(n)
        return fn

    return mkdece


@dec(4)
@dec(3)
@dece(2)
@inject_locals(abcd=1234)
@dece(1)
@dec(2)
@dece(0)
@dec(1)
def checkme(blah=2):
    print("abcd:", abcd)
    print("super", super)


if __name__ == "__main__":
    checkme()
    # checkme()
