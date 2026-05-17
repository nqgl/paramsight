"""``class_swap`` strategy: carry parametrization via a synthetic subclass.

``Child[int](...)`` produces an instance whose ``__class__`` is swapped to a
cached, per-parametrization ``__slots__ = ()`` subclass of ``Child`` carrying
``_paramsight_alias = Child[int]``. Because the synthetic adds no slots, it is
layout-compatible with the origin, so the swap works on slotted / frozen
instances. ``isinstance(inst, Child)`` stays ``True``; ``type(inst) is Child``
becomes ``False`` (the documented trade-off of this strategy).

It survives ``attrs.evolve`` / ``copy`` / ``deepcopy`` (those rebuild via
``type(inst)``, which is the synthetic) and ``pickle`` (via the ``__reduce__``
installed on the synthetic, which reconstructs the origin instance and re-swaps
its ``__class__``; state save/restore is delegated to the normal machinery).
"""

import threading

# (origin, args) -> synthetic subclass. The alias that keys a parametrization
# is itself cached for the process's lifetime, so this never frees -- one small
# type object per distinct parametrization actually instantiated.
_synth_cache: dict[tuple, type] = {}

# Set while we call ``type(...)`` to build a synthetic, so paramsight's patched
# ``__init_subclass__`` can skip it (a synthetic is an implementation detail and
# must not trigger user/base ``__init_subclass__`` hooks or re-install proxies).
_creating = threading.local()


def is_creating_synth() -> bool:
    return getattr(_creating, "flag", False)


def _args_for_getitem(args: tuple):
    # Mirror ``typing._GenericAlias.__reduce__``: a 1-tuple of a non-tuple
    # subscripts as a scalar (``Box[int]`` not ``Box[(int,)]``).
    if len(args) == 1 and not isinstance(args[0], tuple):
        return args[0]
    return args


def _get_or_make(origin: type, args: tuple) -> type:
    key = (origin, args)
    cached = _synth_cache.get(key)
    if cached is not None:
        return cached
    # origin has paramsight's patched __class_getitem__ (-> _GAProxy); the
    # checker can't model that runtime subscriptability.
    alias = origin[_args_for_getitem(args)]  # pyright: ignore[reportIndexIssue]
    ns = {
        "__slots__": (),
        "_paramsight_alias": alias,
        "__module__": getattr(origin, "__module__", None),
        "__qualname__": getattr(origin, "__qualname__", origin.__name__),
        "__reduce__": _synth_reduce,
    }
    _creating.flag = True
    try:
        synth = type(origin)(origin.__name__, (origin,), ns)
    finally:
        _creating.flag = False
    _synth_cache[key] = synth
    return synth


def get_synth(alias) -> type:
    """The synthetic subclass for ``alias`` (a ``_GAProxy``)."""
    return _get_or_make(alias.__origin__, alias.__args__)


def _synth_new(origin: type, args: tuple):
    # origin.__new__(origin) is valid for an arbitrary class at runtime;
    # typeshed's __new__ overloads can't express it.
    obj = origin.__new__(origin)  # pyright: ignore[reportCallIssue]
    object.__setattr__(obj, "__class__", _get_or_make(origin, args))
    return obj


def _synth_reduce(self):
    # 3-tuple ``(callable, args, state)``: pickle calls ``_synth_new`` then
    # applies ``state`` via the instance's normal ``__setstate__`` (or the
    # default slotted-state handling) -- so we don't reimplement state I/O.
    alias = type(self)._paramsight_alias
    getstate = getattr(self, "__getstate__", None)
    state = getstate() if getstate is not None else self.__dict__
    return (_synth_new, (alias.__origin__, alias.__args__), state)
