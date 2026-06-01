# Validated issues

Findings from the review pass that we independently confirmed are real, current
problems (reproduced against HEAD `f77a7ae`, importing this worktree's `src/`
via `PYTHONPATH`). Each was reproduced before landing here.

Status legend: 🔴 open · 🟢 fixed

V1–V5 were fixed in commit `9a20faf` (with regression tests). V6–V7 are valid
but deferred as documented limitations (see their entries for why and the
proposed fix).

| ID | Sev | Source | Summary | Status |
|----|-----|--------|---------|--------|
| V1 | medium | S3a | Auto-filled PEP 696 defaults not coerced in positional branch | 🟢 fixed |
| V2 | low | S2 | Non-descriptor `__init_subclass__` raises `AttributeError` | 🟢 fixed |
| V3 | medium | S1 | Missing `py.typed` marker despite `Typing :: Typed` classifier | 🟢 fixed |
| V4 | medium | S5c/S1 | `class_swap` copy/pickle ignores `__getnewargs__` / arg-taking `__new__` | 🟢 fixed |
| V5 | low | S3b | `Annotated` metadata containing typevars not substituted | 🟢 fixed |
| V6 | medium | S5b | `class_swap` + origin custom `__reduce_ex__` silently loses parametrization | 🔴 open |
| V7 | low | S5a | `class_swap` instance whose `__new__` returns a foreign subclass | 🔴 open |

---

## Details

### V1 — Auto-filled PEP 696 defaults not coerced (positional branch)

- **Severity:** medium · **Source:** S3a · **Location:** `src/paramsight/_paramsight.py` `_build_subs`, positional-arg branch (~line 131)
- **Claim:** When Python auto-fills a later PEP 696 default into `__args__` for a
  partial specialization, the raw default lands there (`class C[T, U = None]` →
  `C[int].__args__ == (int, None)`; a string default stays `"Foo"`). The
  positional branch only substitutes typevars, so it leaves the raw default
  uncoerced, while the bare-default branch coerces via `coerce_to_type_form`.
  `TypeVarValueOption` then mistakes a real `None` default for "unresolved".
- **Validation (repro on HEAD):**
  - `class C[T, U = None]`: `C[int].Ut` (`TypeVarValue`) → raw `None` (should be `NoneType`); `C[int].Uv` (`TypeVarValueOption`) → `None` (reads as unresolved).
  - `class D[T, U = "Foo"]`: `D[int].Uv` → raw `'Foo'` (should be `ForwardRef('Foo')`).
  - Bare path is correct: `class F[U=None]` → `F.Ut` is `NoneType`; `class G[U="Foo"]` → `G.Ut` is `ForwardRef('Foo')`. So the two paths disagree — exactly what the in-code comment at line 136-137 says must not happen.
- **Fix:** apply `coerce_to_type_form` to `args[i]` in the positional branch, the
  same as the default branch. Safe because `coerce_to_type_form` (`typing._type_convert`)
  is a no-op on types, aliases, and typevars (so the auto-filled `U = T` case
  still resolves), and explicit args are already coerced by Python's subscription.
- **Status:** 🟢 fixed (+ regression test in `tests/test_typevar_value.py`).

### V2 — Non-descriptor `__init_subclass__` raises `AttributeError`

- **Severity:** low · **Source:** S2 (review of our own `f77a7ae`) · **Location:** `src/paramsight/aliasclassmethod.py` `_make_patched_init_subclass`
- **Claim:** The `owner_defines_hook` branch does `_orig_init_subclass.__get__(None, cls)(...)`,
  assuming the hook is a descriptor. A non-descriptor callable in the class dict
  (a decorator returning a callable instance) raises `AttributeError` instead of
  being invoked the way native class creation invokes it.
- **Validation (repro on HEAD):** with `__init_subclass__ = Hook()` (a callable
  instance with no `__get__`): native Python invokes it (here raising `TypeError`
  only because this hook needs `cls`), whereas paramsight raises
  `AttributeError: 'Hook' object has no attribute '__get__'`. A non-descriptor
  hook that does not need `cls` works natively but breaks under paramsight.
  (Note `functools.partial` has `__get__` in 3.13, so it is not affected.)
- **Fix:** bind via `__get__` when present, else call the hook directly — matching
  native, which invokes non-descriptor hooks unbound.
- **Status:** 🟢 fixed (+ regression test).

### V3 — Missing `py.typed` marker (PEP 561)

- **Severity:** medium · **Source:** S1 · **Location:** `pyproject.toml` (`Typing :: Typed` classifier) + missing `src/paramsight/py.typed`
- **Claim:** The wheel advertises `Typing :: Typed` but ships no `py.typed`
  marker, so PEP 561 type checkers treat the installed package as untyped and the
  advertised `TypeVarValue` static surface is unavailable downstream.
- **Validation:** classifier present at `pyproject.toml:22`; `src/paramsight/py.typed`
  does not exist.
- **Fix:** add `src/paramsight/py.typed` and ensure the build includes it as
  package data.
- **Status:** 🟢 fixed.

### V4 — `class_swap` copy/pickle ignores construction args

- **Severity:** medium · **Source:** S5c (also raised independently by S1) · **Location:** `src/paramsight/slotted_strategies/class_swap.py` `_synth_new` / `_synth_reduce`
- **Claim:** `_synth_reduce` reconstructs via `_synth_new`, which calls
  `origin.__new__(origin)` with no args and ignores `__getnewargs__` /
  `__getnewargs_ex__`. For a `class_swap` class whose `__new__` requires
  arguments, `copy.copy` / `deepcopy` / `pickle.loads` raise `TypeError`, even
  though the strategy's docstring promises copy/pickle survival.
- **Validation (repro on HEAD):** `class_swap` `Pair[T]` with `__new__(cls, a, b)`
  and `__getnewargs__` → `copy`, `deepcopy`, `pickle` all raise
  `TypeError: Pair.__new__() missing 2 required positional arguments`.
- **Fix:** capture `__getnewargs_ex__` / `__getnewargs__` in `_synth_reduce` and
  forward them through `_synth_new` to `origin.__new__`.
- **Status:** 🟢 fixed (+ regression test in `tests/test_attrs_dataclass_slots.py`).

### V5 — `Annotated` metadata containing typevars not substituted

- **Severity:** low (niche) · **Source:** S3b · **Location:** `src/paramsight/_paramsight.py` `_substitute_typevars`
- **Claim:** For `class M[T](B[Annotated[int, T]])`, substitution misses
  `Annotated.__metadata__`, so even `M[str]` resolves to `Annotated[int, T]` with
  a stale `T`; the unresolved check then raises (or the option descriptor returns
  `None`).
- **Validation (repro on HEAD):** `M[str].val` raises `LookupError` ("resolved to
  `typing.Annotated[int, T]`, which is still unbound"). Root cause:
  `typing.get_origin(Annotated[int, T])` is `int` (a class), so the special-form
  `copy_with` branch is skipped; and `copy_with` would keep `__metadata__`
  unchanged anyway.
- **Fix:** detect `Annotated` (`__metadata__` present), substitute both the
  underlying type and each metadata element, and rebuild via `typing.Annotated`.
- **Status:** 🟢 fixed (+ regression test).

### V6 — `class_swap` + origin custom `__reduce_ex__` silently loses parametrization

- **Severity:** medium · **Source:** S5b · **Location:** `src/paramsight/slotted_strategies/class_swap.py` (synthetic only installs `__reduce__`)
- **Claim:** If the origin overrides `__reduce_ex__`, the synthetic inherits it,
  and pickle/copy call it before `__reduce__`, bypassing `_synth_reduce`. The
  instance is then reconstructed by the origin's reducer — usually as the bare
  origin without `_paramsight_alias` — so later `@takes_alias` / `TypeVarValue`
  lookups silently lose the parametrization.
- **Validation (repro on HEAD):** `class_swap` `Box[T]` with a custom
  `__reduce_ex__` returning `(Box, (self.x,))` → after `pickle` round-trip,
  `f()` returns `(typing.NoDefault,)` instead of `(int,)`. Silent — no error.
- **Proposed fix (not yet applied):** the synthetic should route `__reduce_ex__`
  through paramsight: call the origin's `__reduce_ex__`, then wrap the returned
  callable so the reconstructed instance is re-swapped to the synthetic
  (re-attaching the alias). This must honor the origin's custom reducer rather
  than overriding it wholesale, which is why it is deferred for a careful,
  separately-tested change rather than rushed in this pass.
- **Status:** 🔴 open.

### V7 — `class_swap` instance whose `__new__` returns a foreign subclass

- **Severity:** low (very exotic) · **Source:** S5a · **Location:** `src/paramsight/ga_proxy.py` `_GAProxy.__call__` (~line 92)
- **Claim:** When `__new__` / a metaclass `__call__` returns an instance of a
  *different* slotted subclass, `__call__` always swaps to a synthetic subclass of
  the alias origin — stripping the returned subclass's identity (layout-compatible
  case) or raising `TypeError` (subclass adds slots).
- **Validation (repro on HEAD):** `class_swap` `Base[T]` whose `__new__` returns a
  `SubWithSlot` (adds a slot) → `Base[int]()` raises `TypeError: __class__
  assignment: 'Base' object layout differs from 'SubWithSlot'`. A no-slot subclass
  → identity silently replaced (`isinstance(inst, SubNoSlot)` becomes `False`).
- **Proposed fix (not yet applied):** build the synthetic over the *actual*
  returned type rather than the origin (i.e. `type(result)`), or skip the swap
  when `type(result)` is not the origin. Deferred: the construction patterns that
  trigger this (a constructor returning a foreign subclass) are extremely rare,
  and the correct behavior interacts with the synthetic-cache keying, so it wants
  a dedicated change.
- **Status:** 🔴 open.
