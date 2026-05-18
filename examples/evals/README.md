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
python3 scripts/zagrosi_skills.py eval-suite --examples-dir examples --check-snapshots --output examples/evals/latest.json
```

The report scores plan depth, section readiness, traceability, evidence
quality, and implementation readiness. When `suite.json` exists, it is the
benchmark contract: row names, row order, planning directories, depth, and the
golden snapshot directory come from that file. If a suite file is absent,
`eval-suite` keeps the older glob fallback and discovers valid `codex-plan.md`
fixtures outside `examples/invalid`.

Golden snapshots live in `examples/evals/golden/`. They record the expected
Forge Score component shape for the current valid tracks and are checked by the
test suite so scoring changes are deliberate. Use `--check-snapshots` for
normal verification. Use `--update-snapshots` only after intentionally changing
scoring behavior and reviewing the new golden payloads.

## Comparison Protocol

When comparing Forge against another planning system, use the same input brief,
same codebase snapshot, same allowed tools, and same depth target. Store raw
outputs under a date-stamped directory and score them with the same gates where
possible.

For real-world trials, record the pass with:

```bash
python3 scripts/zagrosi_skills.py e2e-trial-record --planning-dir path/to/plan --name repo-feature-pass
```
