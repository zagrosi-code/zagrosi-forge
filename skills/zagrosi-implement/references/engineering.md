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

Preserve observable behavior and safety invariants. When existing coverage is
weak, add characterization tests before refactoring. Run targeted regression
checks before/after meaningful changes and one full suite at final integration.
Tests should expose behavioral failures, not mirror lines, private structure,
or cosmetic choices. Record material deviations and measured verification.

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

During the existing review, identify the concrete gain: less repeated policy,
fewer concepts to follow, clearer responsibilities, or easier changes. Reject
code golf and unnecessary layers even when line counts improve. Use before/after
regressions for public results, errors, ordering, side effects, and callers that
the cleanup could affect; avoid a new review artifact just to state this.
