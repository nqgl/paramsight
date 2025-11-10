import inspect
from typing import Any

from nicetv._ta_ref_attr import _TA_REF_ATTR
from nicetv.ga_proxy import _GAProxy


class wsuper:  # noqa: N801
    def __init__(self, t: Any = None, obj: Any = None):
        if isinstance(obj, _GAProxy):
            orig = obj.__origin__
        else:
            orig = obj
        self._sup = super(t, orig)
        self.obj = obj

    def __getattr__(self, name):
        got = getattr(self._sup, name)
        if hasattr(got, "__func__"):
            if hasattr(got.__func__, _TA_REF_ATTR):
                ta = getattr(got.__func__, _TA_REF_ATTR)
                return ta.__get__(None, self.obj)
        return got


def _super(
    owner: type | None = None,
    obj: object | None = None,
    *,
    level: int = 1,
):
    """
    Return a `super(...)` bound like zero-arg `super()` from the caller's context
    which handles generic aliases correctly with the takes_alias decorator.
    """
    # from builtins import super

    if owner is not None or obj is not None:
        if owner is None or obj is None:
            raise TypeError("Provide both owner= and obj=, or neither.")
        return super(owner, obj)

    frame = inspect.currentframe()
    if frame is None:
        raise RuntimeError("No Python frame available.")

    try:
        # Walk up to the requested caller
        for _ in range(level):
            frame = frame.f_back
            if frame is None:
                raise RuntimeError("Not enough stack frames.")

        locals_ = frame.f_locals
        code = frame.f_code
        varnames = code.co_varnames

        if not varnames:
            raise TypeError("Caller has no positional locals (not a method?).")

        first_name = varnames[0]
        if first_name not in locals_:
            # Happens e.g. before the function has bound its first arg
            raise TypeError("Caller’s first parameter is not bound yet.")

        first_arg = locals_[first_name]

        try:
            owner_cls = locals_["__class__"]
        except KeyError as exc:
            # This means the function wasn't defined in a class body (no __class__ cell)
            # Using type(first_arg) here would be subtly wrong for inherited methods,
            # so we error out instead of guessing.
            raise TypeError(
                "No __class__ cell found in caller; define the method in a class body "
                "or pass owner=/obj= explicitly."
            ) from exc

        if isinstance(first_arg, _GAProxy):
            return wsuper(owner_cls, first_arg)

        return super(owner_cls, first_arg)

    finally:
        # Help GC break potential reference cycles
        del frame
