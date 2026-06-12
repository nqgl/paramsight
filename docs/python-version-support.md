# Python version support

## Current policy

paramsight targets **Python 3.13 only** (`requires-python = ">=3.13"`). The
implementation deliberately assumes 3.13 semantics to stay focused and robust;
backwards-compatibility shims would dilute that focus today.

This document is a **living register** of every place that 3.13 assumption is
baked in, so that:

- the path to supporting earlier interpreters stays visible, and
- footguns we notice now are written down rather than painfully rediscovered
  later (most won't surface as `ImportError` — they pass type-checking and then
  silently misbehave on an older interpreter).

Nothing here needs action today. When you add a 3.13-only dependency or notice a
version-sensitivity, **add it here.**

## TL;DR — what would actually block a lower floor

1. **PEP 695 syntax is a hard floor at 3.12.** The source is written in
   `class Foo[T]` / `type X = ...` syntax. This is a *parser* feature — it cannot
   be polyfilled or guarded with `try/except`; the module won't even compile on
   3.11. Below 3.12 means a mechanical rewrite to old-style `Generic[T]` +
   explicit `TypeVar`/`ParamSpec`/`TypeVarTuple`.
2. **The typevar `isinstance` checks silently break below 3.13** (Footgun 1).
3. **`typing.NoDefault` and a handful of private internals** need fallbacks —
   mostly easy, since `typing_extensions` backports the public ones.

## Hard floors (structural — cannot be polyfilled)

| Dependency | Min | Where | Notes |
|---|---|---|---|
| PEP 695 generic / type-alias syntax (`class C[T]`, `type X = ...`) | 3.12 | pervasive (15+ classes; `type AliasType = ...` in `type_utils.py`) | **Syntax-level.** No `try/except` escape — the file won't parse. Lowering below 3.12 = rewrite to `Generic[T]` + explicit type-param objects. |
| `cls.__type_params__` | 3.12 | `type_utils.get_parameters` | Authoritative param list for PEP 695 classes. Old-style classes expose `__parameters__` (we already fall back to it). |
| `types.get_original_bases` | 3.12 | `_paramsight._resolve` | Backported as `typing_extensions.get_original_bases`, so really a soft floor *if* the syntax floor is already lifted. |

## Soft floors (runtime features; `typing_extensions` backports all of these)

The fix for each is "import from a compat shim instead of `typing`/`types`."
**But read Footguns** — some carry behavioral differences beyond availability.

| Dependency | Stdlib min | Where | Backport |
|---|---|---|---|
| `typing.NoDefault` (PEP 696 sentinel) | 3.13 | `_NODEFAULT` in `type_utils` & `_paramsight` | `typing_extensions.NoDefault` (and it `is` the stdlib object on 3.13) |
| `TypeVar.__default__` / `has_default()` (PEP 696) | 3.13 | `_get_typevar_default`; all default-coercion logic | via `typing_extensions.TypeVar` |
| `typing.TypeVarTuple` (PEP 646) | 3.11 | variadic binding / substitution | `typing_extensions.TypeVarTuple` |
| `typing.Unpack` | 3.11 | `_is_unpack` | `typing_extensions.Unpack` |
| `typing.ParamSpec` (PEP 612) | 3.10 | ParamSpec handling | `typing_extensions.ParamSpec` |
| `typing.TypeAliasType` (PEP 695 `type` statement) | 3.12 | TypeAliasType rebuild in `_substitute_typevars` | `typing_extensions.TypeAliasType` (but the `type` *syntax* is a 3.12 parser floor anyway) |
| `types.GenericAlias.__iter__` (the `*alias` re-star trick) | 3.11 | `_restarred_unbounded_unpack` rebuilds an unbounded `*tuple[X, ...]` unpacked | none; below 3.11 rebuild via `typing_extensions.Unpack[...]` instead |
| `types.UnionType` (`X | Y`) | 3.10 | union substitution in `_substitute_typevars` | none needed at 3.10+; below that, use `typing.Union` exclusively |

## Footguns (same API, different behavior — the dangerous ones)

### 1. `isinstance`-based typevar detection

`type_utils._is_typevar` / `_is_typevartuple` / `_is_paramspec` use plain
`isinstance(x, typing.TypeVar)` (etc.). **This is correct only because, on 3.13,
`typing_extensions.TypeVar is typing.TypeVar`** — the same object. On 3.10–3.12,
`typing_extensions` ships its *own* backport classes (to provide e.g. PEP 696
defaults before the stdlib had them). A typevar minted by
`typing_extensions.TypeVar(default=...)` is **not** an instance of
`typing.TypeVar`, so a bare `isinstance` returns `False` and the value is
silently treated as a non-typevar — wrong, and quiet.

History: these helpers previously carried a `type(x).__name__ == "TypeVar"`
duck-type fallback for exactly this case. It was dropped on the variadic branch
as dead weight on a 3.13-pinned project. **To lower the floor, restore it** —
either widen the isinstance, or re-add the name check:

```python
def _is_typevar(x):
    return isinstance(x, TypeVar) or type(x).__name__ == "TypeVar"
```

(`_is_unpack` is unaffected: it uses `typing.get_origin(x) is typing.Unpack`,
which is implementation-agnostic.)

### 2. PEP 696 default auto-fill into `__args__`

On 3.13, `class C[T, U = T]` subscripted as `C[int]` reports
`get_args(C[int]) == (int, T)` — Python auto-fills `U`'s default into `__args__`
as the *raw, unsubstituted* typevar. The positional binding in
`_paramsight._build_subs` depends on this (it resolves the auto-filled
`T -> int`). An earlier interpreter using a `typing_extensions` PEP 696 backport
may **not** auto-fill, leaving `get_args(C[int]) == (int,)` and routing through
the default branch instead. Both paths are handled today, but the behavior must
be re-verified, not assumed.

### 3. TypeVarTuple default storage: `*tuple[int, str]` vs `Unpack[tuple[int, str]]`

On 3.13 these two spellings store *different* objects in `__default__` (a bare
`tuple[int, str]` GenericAlias vs an `Unpack`-wrapped alias).
`_typevartuple_default_members` unwraps both. Storage may differ again on other
versions — re-verify if lowering.

### 4. `typing.Callable` vs `collections.abc.Callable`

The two spellings compare unequal by `==`, and only the `typing` one carries
`copy_with`; base-class resolution yields the `collections.abc` spelling. Our
Callable handling normalizes via `get_args`'s `([params], ret)` shape, which is
stable on 3.13. Re-verify the `__args__`/param-list shape on other versions.

## Private `typing` internals we depend on

Version-fragile regardless of the floor — no compatibility guarantee, and the
shapes have changed across 3.x. Worth auditing on **any** version bump, up or
down.

| Internal | Where | Guarded? | Notes |
|---|---|---|---|
| `typing._GenericAlias` | `ga_proxy` (**subclassed!**), `type_utils`, `aliasclassmethod` | partial | The riskiest dependency — `_GAProxy` subclasses it, and its internal attrs / `__reduce__` have shifted across versions. |
| `typing._type_convert` | `type_utils.coerce_to_type_form` | **yes** — `getattr(typing, "_type_convert", None)` + manual `None`→`NoneType` fallback | The model to copy for guarding other internals. |
| `_GenericAlias.copy_with` | `_paramsight._substitute_typevars` | no | Reconstructs `Annotated`/`ClassVar`/… special forms from flat `__args__`. |

## When you DO lower the floor

Rough order of operations:

1. Decide the new floor. **If below 3.12, the PEP 695 source rewrite dominates
   everything else** — scope that first; the rest is noise next to it.
2. Add a `_compat` module that re-exports `TypeVar`, `ParamSpec`, `TypeVarTuple`,
   `Unpack`, `NoDefault`, `get_original_bases` from `typing_extensions` when the
   stdlib lacks them, and import those everywhere instead of `typing`/`types`.
3. Restore the typevar-detection fallback (Footgun 1).
4. Re-verify each Footgun on the *lowest* target interpreter, with the existing
   suite plus version-specific cases.
5. Add the target versions to CI (there is currently no multi-version matrix).
