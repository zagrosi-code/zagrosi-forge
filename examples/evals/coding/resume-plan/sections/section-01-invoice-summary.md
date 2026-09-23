# Invoice summary

## Goal
REQ-001 adds the invoice summary. Non-goals: changing existing actions, report
formatting, dependencies, or unrelated modules.

## Dependencies
None.

## Owned files
Ownership is exclusive to this section.
- src/ledger.py
- tests/test_ledger.py

## Tests first
Case: REQ-001 `test_summary_counts_quantities` checks item_count for two items.
Expected: 5 for quantities 2 and 3; current partial summary omits the field.
Command: `python -m unittest discover -s tests` with PYTHONPATH=src.
Preserve exact receipt/JSON and total assertions. Add uncovered edge cases.

## Implementation contract
Keep `invoice` and `InvoiceManager` signatures and existing outputs/errors.
Summary returns the existing JSON fields plus the sum of item quantities.
Empty carts have item_count 0. Keep aggregate tax truncation and customer values.

## Evidence
Inspected `src/ledger.py`, `tests/test_ledger.py`, and `.planning/spec.md` in the prepared fixture.
Existing tests cover totals and empty receipts. Runtime: Python standard library;
unittest discovers the test module. The partial
summary reuses JSON serialization and lacks item_count. Assumption: existing
item inputs remain valid. Preserve passing tests; complete the failing regression.

## Decisions
Rationale: extend the existing invoice boundary. Alternative: a new public API
would duplicate callers; reject it. Share relevant calculations if that removes
duplication while keeping action validation and serialization behavior.

## Risks
Security/privacy: no external services or secrets. Risk: rounding or exact export
drift. Mitigation: regression tests before/after cleanup and independent oracle.
Migration: none. Rollback: restore prior invoice changes if compatibility fails.

## Review
Verdict: pass
Reviewed: the prepared fixture API, summary contract, owned files, failing test,
compatibility constraints and rollback; implementation verification remains open.

## Acceptance
REQ-001: summary includes correct quantities, existing outputs/errors are unchanged,
regression tests and independent oracle pass, and review/completion are recorded.
