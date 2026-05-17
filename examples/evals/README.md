# Forge Evaluation Fixtures

This directory documents the benchmark surface used by `eval-suite`.

## Current Valid Tracks

- `examples/saas/01-authentication`: Python-style SaaS OAuth foundation.
- `examples/typescript-app/01-auth`: TypeScript OAuth plus preferences.

## Deep Review Track

- `examples/deep-review/zero-downtime-migration`: input scenario for deep-mode
  review-board comparisons. It is intentionally not a completed eval row; use it
  to generate new deep artifacts, then score the result with the same strict
  gates.

## Scoring

Run:

```bash
python3 scripts/zagrosi_skills.py eval-suite --examples-dir examples --output examples/evals/latest.json
```

The report scores plan depth, section readiness, traceability, evidence
quality, and implementation readiness. Invalid fixtures are intentionally
excluded from benchmark rows.

Golden snapshots live in `examples/evals/golden/`. They record the expected
Forge Score component shape for the current valid tracks and are checked by the
test suite so scoring changes are deliberate.

## Comparison Protocol

When comparing Forge against another planning system, use the same input brief,
same codebase snapshot, same allowed tools, and same depth target. Store raw
outputs under a date-stamped directory and score them with the same gates where
possible.

For real-world trials, record the pass with:

```bash
python3 scripts/zagrosi_skills.py e2e-trial-record --planning-dir path/to/plan --name repo-feature-pass
```
