# Forge development audit — 2026-09-23

Baseline: `c665da6` (same source tree as merged `origin/main` at `d9bfdfd`).
User approved saving these findings and continuing implementation. Keep changes
small, preserve public behavior and security boundaries, and test actual failure
cases. Installed footprint and helper timings are not repository size or total
AI-task performance.

## Work and acceptance checks

| ID | Finding and intended change | Regression evidence required |
| --- | --- | --- |
| F01 | Legacy completion accepts conflicting review verdicts. Reuse the shared review parser. | Reject mixed verdicts, blocked findings and malformed fences; preserve valid legacy reviews. |
| F02 | Completed sections stay complete after their contract changes. Bind new records to relevant requirements, dependencies and recorded code. | Relevant edits invalidate completion and block successors; unrelated edits do not; define legacy compatibility. |
| F03 | Scheduling bypasses admission; simultaneous mutable records lose updates. Share readiness and serialize atomic recording. | Block next/parallel work on rejected plans; preserve concurrent records and recoverable pending state after failure. |
| F04 | One compact deep postflight resolved its plan 81 times and dependencies 94 times. Internal checks serialize and recapture their own output. Parse once per command and return structured analyses. | Equivalent results across depths; changed/deleted/replaced inputs invalidate caches; preserve deadlines and detached authority checks. |
| F05 | A 90/2,000-word packet omitted an explicitly linked decision. Follow bounded local contract links and provide packets at task entry. | Include decision/risk/anchor bodies; deduplicate cycles; report broken references and budget conflicts without truncating contracts. |
| F06 | Saved test/review progress does not guide status/resume. Provide a concise, change-aware resume brief. | Resume unchanged work at its saved stage; changed inputs require relevant revalidation; retain unrelated user changes. |
| F07 | Cache equality ignores development junk; failed publication can delete the working installation. Prune traversal and replace recoverably. | Repair polluted caches; preserve old installation on publication failure; retain package/source safety. Existing PR #3 overlaps and must not be merged wholesale. |
| F08 | Project-specific privileged integration is embedded in generic runtime. Isolate an explicit trusted adapter. | Ordinary commands do not load it; preserve immutable contracts, authority/source checks and command compatibility. |
| F09 | Coding trials measure requested cleanup, not cleanup discovered during feature work; resume is only narrative and fixtures are Python-only. Extend focused outcome trials. | Feature-time relevant cleanup, independent before/after behavior checks, real persisted resume, one additional stack, honest telemetry and provenance. |

## Delivery order

1. Completion correctness and atomic state; shared admission.
2. Parsed-plan reuse and direct analysis results.
3. Linked context and real resume guidance.
4. Installer repair/recovery and trusted adapter isolation.
5. Representative coding trials; integrated regression and release checks.

## Implementation outcomes

| IDs | Delivered | Main regression coverage |
| --- | --- | --- |
| F01–F03 | Shared review/admission checks, contract-bound completion, serialized recording and atomic state publication. | Conflicting reviews, changed requirements/dependencies, legacy records, concurrent writes, failed publication and pending verification. |
| F04 | Command-local parsed-input reuse and direct analysis results; removed internal JSON recapture. | All depths; changed, deleted, replaced and linked inputs; copy isolation; preserved deadline and detached checks. A diagnostic deep fixture resolves its descriptor once instead of 81 times. |
| F05–F06 | Complete bounded local linked contracts in entry packets; saved stage, changed inputs and actionable resume guidance. | Anchors, cycles, missing links, budget conflicts, backtick labels, pending verification overriding stale success. |
| F07 | Pruned package inventory and locked, staged cache replacement with recoverable previous installation. | Junk repair, symlink rejection, source changes during copying, failed publication/rollback, interrupted recovery and source/recovery overlap. |
| F08 | Lazy trusted adapter for privileged project constants and executable policy. | Ordinary commands avoid adapter loading; legacy constant access and frozen authority/wire checks retain their behavior. |
| F09 | Feature-time cleanup trial, protected unrelated code, native JavaScript trial, and an admitted persisted failing-test resume case. | All resume depths; preserved original checkpoint/test; independent Python/JavaScript inputs and mutation detection; provenance and independent cleanup review. |

Independent review reproduced two false passes in the trial evaluator: candidate
input mutation contaminated expected outputs, and resumed work could delete its
original regression/checkpoint. Both now have negative regression cases. The
existing process probe test also uses a single long-lived process to avoid an
unnecessary shell-child timing dependency; timeout assertions remain intact.

### Evidence boundaries

- New completion records bind relevant contracts. Legacy records remain usable
  and explicitly unbound. Code drift prompts revalidation rather than reopening
  every earlier section when a later section legitimately edits shared code.
- Cache recovery and the adapter preserve the existing trust boundaries. The
  adapter remains packaged; this change makes it lazy, not a separate plugin.
- Parsing counts and local helper timings are not whole-task latency, token
  savings, repository shrinkage or broad model-quality measurements.
- Trials are isolated workspaces, not security sandboxes. Independent review is
  an explicit attestation bound to source hashes, not authenticated identity.
- The real deep trial revealed wording sensitivity in existing planning checks
  (`Existing tests`, `Case: REQ-...`, backticked commands). Its admitted contract
  follows the supported format; accepting equivalent wording is follow-up work.

### Real deep feature trial

A separate implementing agent received the ordinary summary-feature request and
used Forge planning/setup/progress/recording/final verification at deep depth.
An independent agent inspected and reran its evidence:

- Original implementation: 12 characterization tests passed. Six new feature
  tests then produced eight expected unsupported-summary errors.
- Completed implementation: all 18 tests passed. Three duplicated arithmetic
  loops became one private helper; the unrelated report module stayed unchanged.
- Largest function: 26 → 18 lines. Total source: 50 → 52 lines including the new
  feature. No added module, dependency or public API break.
- Original immutable plugin/evaluator snapshot: all 633 oracle assertions and
  full admitted deep workflow, completion, provenance and cleanup review passed.
- The repaired oracle independently rechecked the unchanged candidate: all 1,266
  assertions passed, including input preservation. This is a separate recheck;
  the original trial's provenance was not rewritten.

Candidate fingerprint:
`e80a856f74b1f61fcd18249fb4140ff267ffa6181dcc31b3df762e799c36ecde`.
Repaired oracle SHA-256:
`00becc166f3fb5e55bfa7e39823304f8cca6bd0f16f52b18b4360bae9ffa8786`.
Local raw evidence: `/private/tmp/forge-summary-real-20260923/`.
This one Python feature trial does not establish outcomes for every task or
language. Model/token telemetry was unavailable.

## Final validation

Local macOS, Python 3.12.13 and Node 24.18.0:

- Full suite: **911 passed, 1 skipped** in 167.09 seconds.
- Runtime/test manifest check: passed.
- Strict doctor: score 100, no findings.
- Release check: all nine checks passed.
- Plugin scanner: passed manifest, marketplace, skills and assets checks.
- Ruff undefined/unused checks and Git whitespace checks: passed.
- Independent runtime, context, installer, adapter and trial review: no remaining
  material findings after the reproduced defects were corrected.

The compatibility CI selection includes the new portable regressions. Remote CI,
merge and installation into the active user cache are separate delivery steps;
these results establish the local development state only.
