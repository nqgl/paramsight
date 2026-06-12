# Code-review tracking

This directory tracks external code-review passes (run with `codex review`) over
the recent paramsight work, and what we did with each finding.

Two passes are recorded:
- The original review of the code-review branch — see [`validated-issues.md`](validated-issues.md)
  (V1–V7) and [`rejected-suggestions.md`](rejected-suggestions.md) (R1).
- The review of the `variadic_substitution` merge — see
  [`merge-review-findings.md`](merge-review-findings.md) (MV-1–MV-5).

## Process

1. Run several `codex review` sessions in the background, each over a specific
   target (a single commit, or a cumulative diff against a base). Raw output is
   captured under `raw/`.
2. Triage every finding the reviews surface:
   - **Validate** it against the actual code — is it a real, current problem?
   - **Legitimate & actionable** → logged in [`validated-issues.md`](validated-issues.md),
     then fixed (commit linked).
   - **Not legitimate** (false positive, already handled, intended behavior) →
     logged in [`rejected-suggestions.md`](rejected-suggestions.md) with the
     reason we rejected it. We keep these so the same suggestion doesn't get
     re-litigated later.
3. Fixes are made on the current branch (`wt/paramsight/review-branch`).

Nothing from a review is taken at face value: each item is independently checked
before it lands in either ledger.

## Review sessions

Run from the `review-branch` worktree (`f77a7ae`). `--base X` reviews `X..HEAD`;
`--commit X` reviews the diff introduced by `X`.

| ID | Target | Scope | Raw log |
|----|--------|-------|---------|
| S1 | `--base 63fab37` | **Headline:** full diff since the May-10 typevar-tracing overhaul (`63fab37`) → now | `raw/S1-base-63fab37.log` |
| S2 | `--commit f77a7ae` | Stop GA-proxy patch from eating owner's `__init_subclass__` | `raw/S2-f77a7ae.log` |
| S3 | `--commit c3b2653` | Resolve typevar defaults/values; add `TypeVarValueOption` | `raw/S3-c3b2653.log` |
| S4 | `--commit 664e721` | Reintegrate slotted-class `__orig_class__` handling | `raw/S4-664e721.log` |
| S5 | `--commit 95b9b19` | class_swap strategy + decorator API for slotted generics | `raw/S5-95b9b19.log` |
| S6 | `--commit 41077a0` | Gate slotted-instance raise on the class having free typevars | `raw/S6-41077a0.log` |
| S7 | `--commit b8f2a6f` | Point the slotted-instance error at the decorator API | `raw/S7-b8f2a6f.log` |

## Outcome

All 7 sessions ran and were triaged. Findings (after independent validation):

- **8 distinct real issues** confirmed (deduping S5c, which S1 also raised):
  V1–V7 in [`validated-issues.md`](validated-issues.md).
- **5 fixed** with regression tests in commit `9a20faf` (V1 defaults coercion,
  V2 non-descriptor `__init_subclass__`, V3 `py.typed`, V4 class_swap newargs,
  V5 Annotated metadata).
- **2 initially deferred**, since fixed (V6 class_swap `__reduce_ex__`, V7
  class_swap foreign-subclass return) — see their entries for the applied
  fixes and regression coverage.
- **1 rejected** in [`rejected-suggestions.md`](rejected-suggestions.md) (R1 was
  real at commit `664e721` but already remediated by `41077a0`).
- Sessions S6 and S7 found nothing.

## Status

- [x] All sessions complete
- [x] All findings triaged
- [x] All fixes landed (V1–V5 in `9a20faf`; V6–V7 in the V6/V7 fix commit)
