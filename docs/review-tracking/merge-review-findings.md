# Merge-review findings (variadic merge)

After merging `variadic_substitution` into the code-review branch (merge commit
on `wt/paramsight/merge_reviewed_and_variadic`), two `codex review` passes were
run on the merge:

- **M1** — `codex review --base 9594225` (review-branch tip → merge): the diff
  variadic adds on top of the reviewed branch. Raw: `raw/M1-base-review-tip.log`.
- **M2** — `codex review --base c3b2653` (common ancestor → merge): everything
  since the two branches diverged. Raw: `raw/M2-base-common-ancestor.log`.

Both reviews focused entirely on the **variadic feature surface** (TypeVarTuple /
ParamSpec / Concatenate) and flagged nothing in the review-branch fixes (V1–V7) —
a good signal the merge is clean on that side. M1 and M2 overlapped heavily;
deduped, they gave **5 distinct, confirmed issues**, all in
`src/paramsight/_paramsight.py`. Each was reproduced against the merged tree
before fixing, and all five are now fixed with regression tests in
`tests/test_typevar_value.py`.

Status legend: 🟢 fixed

| ID | Sev | Source | Summary | Status |
|----|-----|--------|---------|--------|
| MV-1 | medium | M1+M2 | Fixed unpacked tuple (`*tuple[int,str]` / `Unpack[tuple[...]]`) not flattened | 🟢 fixed |
| MV-2 | medium | M2 | Empty TypeVarTuple marker `C[int, ()]` leaks as `((),)` | 🟢 fixed |
| MV-3 | medium | M1+M2 | ParamSpec list members not coerced (`[None,"Foo"]` leaks raw) | 🟢 fixed |
| MV-4 | medium | M1+M2 | ParamSpec PEP 696 list default `**P=[T]` not normalized | 🟢 fixed |
| MV-5 | medium | M1+M2 | `Concatenate[int, P]` with `P=...` drops the `int` prefix | 🟢 fixed |

---

## Details

### MV-1 — Fixed unpacked tuple not flattened when binding `*Ts`

- **Repro:** `class V[*Ts](B[tuple[*Ts]])` → `V[*tuple[int, str]].x_type` gave
  `tuple[*tuple[int, str]]` (nested) instead of `tuple[int, str]`; the
  `Unpack[tuple[int, str]]` spelling gave `NoDefault`.
- **Root cause:** `_is_unpack` only recognizes `typing.Unpack`. The `*tuple[...]`
  spelling is a `types.GenericAlias` with `__unpacked__=True` (origin `tuple`),
  so `_subst_args` appended it whole instead of flattening its members.
- **Fix:** new `_fixed_unpack_members` helper detects both spellings of a
  *fixed-length* unpacked tuple and returns its (substituted, coerced) members;
  `_subst_args` flattens them. Because subscription args flow through `_subst_args`
  before `_build_subs`, this fixes both the flattening and the `*Ts` absorption.

### MV-2 — Empty TypeVarTuple marker with ordinary params

- **Repro:** `class M[T, *Ts](B[tuple[T, *Ts]])` → `M[int, ()].x_type` gave
  `tuple[int, ()]` instead of `tuple[int]`.
- **Root cause:** an explicit empty `*Ts` next to ordinary params is stored by
  CPython as a bare `()` placeholder in `__args__`; the absorbed-run slice treated
  it as one absorbed argument, so `*Ts` became `((),)`.
- **Fix:** in `_build_subs`, treat an absorbed run equal to `((),)` as empty.

### MV-3 — ParamSpec list members not coerced

- **Repro:** `class C[**P](B[Callable[P, int]])` → `C[[None, "Foo"]]` gave
  `Callable[[None, 'Foo'], int]` while the shorthand `C[None, "Foo"]` gave
  `Callable[[NoneType, ForwardRef('Foo')], int]`; the two spellings disagreed.
- **Fix:** `_normalize_paramspec_arg` coerces each member with
  `coerce_to_type_form` before substituting (the ParamSpec analog of V1).

### MV-4 — ParamSpec PEP 696 list default not normalized

- **Repro:** `class C[T = int, **P = [T]](B[Callable[P, str]])` → bare `C` gave
  `Callable[[T], str]` (raw `T`, reported unresolved) instead of `Callable[[int], str]`.
- **Root cause:** `bind_unfilled` special-cased only TypeVarTuple defaults; a
  ParamSpec list default fell through to the scalar path, which leaves lists alone.
- **Fix:** route a ParamSpec default through `_normalize_paramspec_arg`, the same
  normalization an explicit ParamSpec arg gets.

### MV-5 — Concatenate prefix dropped for an ellipsis tail

- **Repro:** `class C[**P](B[Callable[Concatenate[int, P], str]])` → `C[...]` gave
  `Callable[..., str]`, dropping the required leading `int`.
- **Root cause:** `_subst_concatenate` returned a bare `Ellipsis` when the tail
  ParamSpec resolved to `...`, discarding the prefix.
- **Fix:** when the tail is `...` and the prefix is non-empty, rebuild
  `Concatenate[*prefix, ...]` (a valid parameter spec); only a prefix-less tail
  collapses to a bare `...`.
