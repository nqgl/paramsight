# Rejected review suggestions

Findings we investigated and decided **not** to act on: false positives, things
already handled elsewhere, or intended behavior. Kept on record so the same
suggestion isn't re-litigated.

Most of the reviews ran per-commit (`codex review --commit <SHA>`), so a finding
can be entirely valid *as of that commit* yet already remediated by a later one.
Those land here, because they are not actionable against current HEAD.

| ID | Source | Summary | Reason rejected |
|----|--------|---------|-----------------|
| R1 | S4 | Slotted-instance guard not gated on free typevars | Already remediated by a later commit (`41077a0`) |

---

## Details

### R1 — Gate slotted-instance errors on free typevars

- **Source:** S4 (review of commit `664e721`) · **Location cited:** `aliasclassmethod.py:146-149` (as of `664e721`)
- **Claim:** The slotted-instance guard raises for slotted instances whose class
  has no remaining type parameters (e.g. `@define class Concrete(Base[int])` →
  `Concrete().f()`), even though resolution against `type(instance)` would still
  return `(int,)` from the MRO; a non-generic slotted class is also rejected.
- **Investigation:** This is a correct observation **about commit `664e721`**, but
  the very next relevant commit, `41077a0` ("gate slotted-instance raise on the
  class having free typevars"), fixes exactly this — and the S6 review of
  `41077a0` independently confirmed the fix and its regression coverage.
  Reproduced against current HEAD:
  - `@define class Concrete(Base[int])` → `Concrete().f()` returns `(int,)` ✓
  - mirrored `TypeVarValue`: `@define class VConcrete(VBase[int])` → `VConcrete().Type` returns `int` ✓
  - non-generic slotted `@takes_alias` → returns normally ✓
  The current guard gates on `get_parameters(cls)` (free typevars) in both
  `_TakesAlias.__get__` and the mirrored `TypeVarValue` path.
- **Why rejected:** Already fixed by `41077a0`; not actionable on current HEAD.
  Useful as confirmation that `41077a0` did its job.
