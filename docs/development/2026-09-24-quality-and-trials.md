# Forge quality and complete-task trials

Baseline: merged `204fcb4`. User approved all five priorities on 2026-09-24.

| Work | Acceptance |
| --- | --- |
| Verification | One shared interpretation accepts equivalent visible instructions; hidden, missing or contradictory evidence fails. Exercise admission, readiness and traceability at every depth. |
| Workflow simplification | Separate responsibilities in project validation and detached setup/recording; preserve diagnostics, authority checks, atomic publication and rollback. Run existing behavior coverage before and after. |
| Complete-task measurement | Repeat feature, cleanup and resume trials across lean/standard/deep. Preserve individual failures, source identity, elapsed time and available usage; distinguish execution from review and unknown telemetry. |
| Cleanup outcomes | Add broader realistic feature coverage with tangled existing code and weak tests. Independent behavior checks and review must detect regressions, useful cleanup and unrelated edits. |
| Installer reconciliation | Compare PR #3 against current main by ancestry/content and tests. Extract only missing useful fixes; retire superseded work after documenting evidence. |

Ownership: verification reader/callers; project and detached workflows; trial
tools/fixtures; installer reconciliation report. Changes in disjoint files run
in parallel. Regenerate the runtime manifest after integration; run focused tests,
independent review, one full suite, release checks and cross-platform CI.

Measurements describe observed trials, not a guaranteed speedup. Readability and
behavior preservation take precedence over line counts. Existing authorization
covers commit, push, PR, merge and local plugin refresh after verification.

## Results

- Shared visible verification now serves admission, traceability and packets.
  Independent review also fixed escaped-backtick comments and hidden source/plan
  requirements. 232 focused regression tests passed after those fixes.
- Project validator: 260→91 lines; detached setup: 225→164; detached recording:
  233→96. The three modules lose 66 lines / 3,834 bytes. Nineteen independently
  compared project scenarios retained identical results; authority/publication
  checkpoint ordering was preserved.
- Configuration changes preserve unrelated bytes or refuse unsupported syntax;
  updates are locked and atomic, backups restrictive, previews limited to owned
  settings. Explicit package membership excludes undeclared files and rejects
  hardlinks, symlinks and missing members. CI checks the staged member inventory.
- New multi-module CSV preview fixture has 638 independent assertions, weak
  initial tests and a protected unrelated module. The Node oracle now pins public
  exports and exception constructors. Negative cases demonstrate that vacuous
  candidate tests cannot hide behavior regressions.
- First integrated full suite: 1,029 passed, 1 skipped. The final full suite and
  complete-task measurements remain in progress; this is not a release claim.

### Experiment protocol

Frozen baseline: `/tmp/forge-quality-baseline-20260924` at `204fcb4`.
Frozen reviewed candidate: `/tmp/forge-quality-candidate-final-20260924`.
Each primary matrix repeats feature, cleanup and resume at all three depths twice
(18 attempts). An additional six runs cover CSV imports and Node at all depths.
Independent external cleanup reviews bind source hashes and rerun characterization
and candidate tests. All attempts retain individual source/runner/evaluator
fingerprints, logs and reported usage. External oracle/review time is separate.

The configured CLI model was rejected before coding in the first 18-attempt
cohort. A supported CLI built-in configuration passed a smoke check; subsequent
runs use identical `--ignore-user-config` arguments without editing user settings.
The earlier candidate cohort was cancelled after two review findings and remains
recorded separately. Neither cohort establishes coding-task speed.

The baseline harness rejects legitimate `.gitignore` rules for local planning;
the candidate accepts only `.planning/` (or `/.planning/`) and comments. Preserve
that baseline failure rather than retroactively changing its evaluator. Candidate
prompts also name the actual test interpreter. These harness changes and concurrent
local work prevent treating timing differences as a controlled speedup estimate.
Token data comes from runner JSONL; the exact built-in model identity and retry
counts remain unknown. Two repetitions are descriptive observations.
