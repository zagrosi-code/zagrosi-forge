# Engineering Standard — All Depths

Trace the actual flow and callers before choosing a change. Reuse existing
code, standard-library/native facilities, then installed dependencies. Prefer
meaningful names, direct control flow, and cohesive functions/modules; readability
beats code golf. Delete unused/duplicate code and unnecessary layers rather than
adding wrappers or speculative frameworks.

Address encountered bad code, mixed responsibilities, and oversized modules in
the task's execution path. Fix shared causes once. Plan substantive cleanup with
the feature; implement it without extra permission when already authorized. If
additional files are needed, update ownership/contracts and serialize overlapping
work before editing. Do not hide necessary repairs behind an obsolete file fence
or refactor unrelated systems. Planning-only requests still stop before coding.
If extending encountered duplication, characterize uncertain behavior first,
then repair the shared cause. Weak coverage calls for characterization, not
another copy. Defer only for a concrete scope or compatibility constraint,
explained in the existing review. Cache removal is housekeeping, not code cleanup.

Preserve observable behavior and safety invariants. When existing coverage is
weak, add characterization tests before refactoring. Run targeted regression
checks before/after meaningful changes and one full suite at final integration.
Derive compatibility tests from the unchanged source and its callers, not the
planned replacement. For meaningful mutable refactors, use the shared
[compatibility checks](compatibility.md) to capture a passing baseline and rerun
unchanged checks against the candidate. Existing tests may suffice; record check
provenance honestly. Keep intended changes in separate red/green regressions.
When full public compatibility is promised, enumerate names
and signatures, including re-exported types/dependencies, and run those assertions
against the baseline before moving code. Keep them passing through the move.
Neither a plan assumption nor a new facade or `__all__` may narrow that promise.
Internal non-use does not prove that callers no longer need it.
Tests should expose behavioral failures, not mirror lines, private structure,
or cosmetic choices. Record material deviations and measured verification.

Before PR delivery, use the existing review to inspect the diff and relevant
callers for introduced or encountered clutter. Fix justified local problems and
refresh affected checks before the final integration result. Record unrelated
debt without expanding the PR indefinitely. An explicitly requested repository
cleanup uses [Cleanup](../../zagrosi-cleanup/SKILL.md).

## Useful simplification

- **Earn a helper.** Replace an internal `_label(prefix, name)` used once only to
  return `f"{prefix}: {name}"` with that expression. Extract repeated amount
  parsing into `parse_amount` when it owns a shared validation rule. Preserve
  public entrypoints, conversion/error order, and single-use iterators.
- **Split responsibilities.** For an import godfile that parses CSV, validates
  amounts, calculates totals, and writes reports, keep the public entrypoint as
  a short composition of parsing, calculation, and output. Reuse existing module
  boundaries; move cohesive behavior with its tests. Renaming a 300-line method
  or distributing arbitrary chunks among new classes does not simplify it.
- **Keep dependencies direct.** Let stateful workflows call pure calculation;
  don't make a calculation construct the workflow to call back into itself.
  Remove such reverse dependencies instead of hiding cycles with delayed imports.

During the existing review, identify the concrete gain: less repeated policy,
fewer concepts to follow, clearer responsibilities, or easier changes. Reject
code golf and unnecessary layers even when line counts improve. Use before/after
regressions for public results, errors, ordering, side effects, and callers that
the cleanup could affect; avoid a new review artifact just to state this.
