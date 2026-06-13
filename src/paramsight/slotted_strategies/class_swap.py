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

Two non-default shapes are handled explicitly:

- A constructor that returns a *foreign subclass* instance (a factory-style
  ``__new__`` / metaclass ``__call__``): the synthetic is built over the class
  actually returned, preserving its identity and layout (see ``get_synth``).
- A class with its own ``__reduce__`` / ``__reduce_ex__``: the custom reducer
  is honored -- it controls reconstruction -- and the payload is wrapped so the
  rebuilt object is re-swapped, re-attaching the parametrization the reducer
  doesn't know about (see ``_rewrap_reduced``).
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


def _unswapped(cls: type) -> type:
    """``cls`` with any synthetic layer removed -- the class the user actually
    defined. Keeps re-swaps (an already-swapped instance fed back through a
    constructor, or a reduce round-trip) from stacking synthetic-on-synthetic."""
    while "_paramsight_alias" in cls.__dict__:
        cls = cls.__bases__[0]
    return cls


def _get_or_make(origin: type, args: tuple, base: type | None = None) -> type:
    """The synthetic subclass carrying ``origin[args]``, built over ``base``
    (default: the origin itself). ``base`` differs when a constructor returned
    a *foreign subclass* instance: the synthetic then subclasses that class,
    preserving its identity (isinstance, methods -- and layout: a subclass may
    add slots the origin lacks) while still carrying the origin's alias."""
    base = origin if base is None else base
    key = (origin, base, args)
    cached = _synth_cache.get(key)
    if cached is not None:
        return cached
    # origin has paramsight's patched __class_getitem__ (-> _GAProxy); the
    # checker can't model that runtime subscriptability.
    alias = origin[_args_for_getitem(args)]  # pyright: ignore[reportIndexIssue]
    ns = {
        "__slots__": (),
        "_paramsight_alias": alias,
        "__module__": getattr(base, "__module__", None),
        "__qualname__": getattr(base, "__qualname__", base.__name__),
        "__reduce__": _synth_reduce,
    }
    if base.__reduce_ex__ is not object.__reduce_ex__:
        # The class customizes ``__reduce_ex__``. pickle/copy call it *before*
        # ``__reduce__``, so without a shim the custom reducer would bypass the
        # synthetic re-swap and the rebuilt instance would silently lose its
        # parametrization.
        ns["__reduce_ex__"] = _synth_reduce_ex
    _creating.flag = True
    try:
        synth = type(base)(base.__name__, (base,), ns)
    finally:
        _creating.flag = False
    _synth_cache[key] = synth
    return synth


def get_synth(alias, instance_cls: type | None = None) -> type:
    """The synthetic subclass for ``alias`` (a ``_GAProxy``). Pass the class of
    the instance being tracked when it may differ from the alias origin (a
    ``__new__`` / metaclass ``__call__`` that returns a foreign subclass): the
    synthetic is then built over that class, so the swap preserves the
    instance's identity instead of stripping it (or failing on layout)."""
    base = None if instance_cls is None else _unswapped(instance_cls)
    return _get_or_make(alias.__origin__, alias.__args__, base)


def _synth_new(
    origin: type,
    args: tuple,
    newargs: tuple = (),
    newkwargs=None,
    base: type | None = None,
):
    # Honor a custom / argument-taking ``__new__`` (captured via
    # ``__getnewargs_ex__`` / ``__getnewargs__`` in ``_synth_reduce``) so classes
    # that need construction arguments still round-trip through copy/pickle.
    # ``base`` is the class the instance actually had (a foreign subclass when a
    # constructor returned one): reconstruct with the same class so the re-swap
    # stays layout-compatible. ``base.__new__(base, ...)`` is valid for an
    # arbitrary class at runtime; typeshed's __new__ overloads can't express it.
    base = origin if base is None else base
    obj = base.__new__(base, *newargs, **(newkwargs or {}))  # pyright: ignore[reportCallIssue]
    object.__setattr__(obj, "__class__", _get_or_make(origin, args, base))
    return obj


def _synth_reduce(self):
    # 3-tuple ``(callable, args, state)``: pickle calls ``_synth_new`` then applies
    # ``state`` via the instance's normal ``__setstate__`` (or the default
    # slotted-state handling) -- so we don't reimplement state I/O. Forward any
    # ``__getnewargs_ex__`` / ``__getnewargs__`` through the ``args`` slot so an
    # arg-taking ``__new__`` is reconstructed with its required arguments rather
    # than failing on a bare ``origin.__new__(origin)``.
    synth = type(self)
    alias = synth._paramsight_alias
    base = synth.__bases__[0]
    if base.__reduce__ is not object.__reduce__:
        # The class brings its own ``__reduce__`` (which the synthetic's would
        # otherwise shadow); honor it -- it controls reconstruction -- and just
        # re-attach the parametrization around its payload.
        # Unbound call: class attribute access yields the plain function, and
        # ``self`` IS a ``base`` instance; pyright models it as already bound.
        return _rewrap_reduced(base.__reduce__(self), synth)  # pyright: ignore[reportCallIssue]
    newargs: tuple = ()
    newkwargs = None
    getnewargs_ex = getattr(self, "__getnewargs_ex__", None)
    if getnewargs_ex is not None:
        newargs, newkwargs = getnewargs_ex()
    else:
        getnewargs = getattr(self, "__getnewargs__", None)
        if getnewargs is not None:
            newargs = getnewargs()
    getstate = getattr(self, "__getstate__", None)
    state = getstate() if getstate is not None else self.__dict__
    return (
        _synth_new,
        (alias.__origin__, alias.__args__, newargs, newkwargs, base),
        state,
    )


def _synth_reduce_ex(self, protocol):
    # Installed on the synthetic only when the underlying class overrides
    # ``__reduce_ex__`` (see ``_get_or_make``): call that override -- it
    # controls reconstruction -- then re-attach the parametrization around
    # whatever payload it produced.
    synth = type(self)
    base = synth.__bases__[0]
    # Unbound call, same as the ``__reduce__`` case in ``_synth_reduce``.
    return _rewrap_reduced(base.__reduce_ex__(self, protocol), synth)  # pyright: ignore[reportCallIssue]


def _rewrap_reduced(reduced, synth: type):
    """Wrap a class's own reduce payload so the object it reconstructs gets
    re-swapped onto the synthetic, re-attaching the parametrization the
    class's reducer doesn't know about. The state / list / dict items of the
    payload are left to the normal machinery. The string (global-name) form
    pickles by reference to a module global -- nothing to re-attach -- and
    passes through untouched."""
    if not isinstance(reduced, tuple) or len(reduced) < 2:
        return reduced
    alias = synth._paramsight_alias
    func, fargs, *rest = reduced
    # A reducer that embeds ``type(self)`` (e.g. one delegating to copyreg's
    # ``__newobj__`` / ``_reconstructor``) would try to pickle the synthetic
    # class by reference, which fails loudly -- its qualname resolves to the
    # real class, and pickle rejects the mismatch. Substitute the real class;
    # the re-swap puts the synthetic back at reconstruction time.
    base = synth.__bases__[0]
    fargs = tuple(base if a is synth else a for a in fargs)
    return (
        _synth_rebuild,
        (func, fargs, alias.__origin__, alias.__args__),
        *rest,
    )


def _synth_rebuild(func, fargs, origin: type, args: tuple):
    # The reconstruction half of ``_rewrap_reduced``: run the class's own
    # reconstructor, then re-swap the result onto the synthetic. An object the
    # reducer rebuilt as something else entirely (not an ``origin`` at all) is
    # left alone -- the strategy can't track it, matching how an untracked
    # construction behaves.
    obj = func(*fargs)
    if isinstance(obj, origin):
        object.__setattr__(
            obj, "__class__", _get_or_make(origin, args, _unswapped(type(obj)))
        )
    return obj


def uses_class_swap(cls: type) -> type:
    """Class decorator: track parametrization by swapping each instance's
    ``__class__`` to a cached per-parametrization synthetic subclass.

    No field pollution; survives ``attrs.evolve`` / ``copy`` / ``pickle``.
    Trade-off: ``type(inst) is Cls`` becomes ``False`` (``isinstance`` still
    works). Equivalent to ``_paramsight_slots = "class_swap"`` in the class
    body.
    """
    cls._paramsight_slots = "class_swap"  # type: ignore[attr-defined]
    return cls
