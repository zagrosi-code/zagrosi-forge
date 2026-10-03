# Behavior preservation comparison — 2–3 October 2026

**New Forge and previous Forge each accepted 8 of 12 attempts; plain Codex accepted
9 of 12. This study does not establish better generated-code quality from the new
preservation mechanism.** New Forge used more time and tokens than either other
arm. Those costs are observations, not a general performance result.

[Archived machine-readable results](https://github.com/zagrosi-code/zagrosi-forge/blob/75922e4c035acf04c4906d4d6a4c4525b0fbedc9/examples/evals/coding/behavior-contract-results-2026-10-02.json) retain all
36 attempts, source identities, independent judgments, usage and the separate
mechanism audit. The [earlier follow-up](quality-followup-2026-10-02.md) remains
unchanged. No candidate was repaired or rerun.

## Code acceptance and review

Common acceptance requires behavior, independent correctness/cleanup review,
final file scope, dependencies, source identity and writer completion. Forge
workflow completion is measured separately. In this experiment the common-quality
and delivery totals coincide because all 24 Forge workflows completed.

| Task | Previous Forge | New Forge | Plain Codex |
|---|---:|---:|---:|
| Layered configuration | 3/3 | 3/3 | 3/3 |
| Atomic file publication | 1/3 | 0/3 | 1/3 |
| Streaming records | 3/3 | 3/3 | 3/3 |
| Event subscriptions | 1/3 | 2/3 | 2/3 |
| **Accepted** | **8/12** | **8/12** | **9/12** |

All 36 writers passed their own tests and finished within the 900-second deadline.
Reviewers found useful cleanup in all 36 candidates. Eleven nevertheless failed
correctness or compatibility; useful cleanup does not compensate for regressions.

The frozen automated behavior checks passed 10 previous, 11 new and 11 plain
candidates. They caught four event implementations that bound callbacks to an
internal registration object instead of invoking them as ordinary functions.
Independent reviewers then rejected seven additional publication candidates,
including implementations that:

- discarded special permission bits or changed new-file permissions;
- rejected valid long destination names by embedding the entire name in a
  temporary-file prefix;
- leaked staging files when writing or setting their permissions failed.

Those seven original green automated results remain visible beside the failing
reviews. The reviewers used temporary-copy probes and preserved candidate bytes.
The oracle was not expanded and the experiment was not regraded after seeing
these findings.

Across the twelve comparisons, reviewers preferred previous Forge alone four
times, new Forge alone four times and plain Codex alone twice. One configuration
comparison was a three-way tie; one publication comparison had no acceptable
candidate. These judgments concern these candidates, not general model rankings.

## What the preservation mechanism actually did

All twelve new-Forge attempts declared required preservation checks. Their final
pairs and copied completion records match the selected check bytes/modes,
commands, selected original fixture source and source/contract identities. That establishes
final mechanical consistency, not an uninterrupted preservation process.

| Audited observation | New Forge attempts |
|---|---:|
| Final pair mechanically valid | 12/12 |
| Any captured baseline before the first implementation edit | 11/12 |
| Final selected baseline captured before the first implementation edit | 3/12 |
| Final baseline recaptured after restoring original source | 9/12 |
| Writer deleted earlier receipt history | 8/12 |
| Observed implementation regression repaired because a preservation pair failed | 0/12 |

Eleven writers captured an initial baseline before their first implementation
edit; eight of those baselines were subsequently superseded. The eight deleted
histories remain observable in retained command traces; their
final receipts do not preserve those earlier attempts. Some writers hit check
identity or snapshot failures, restored original source, and rebuilt evidence.
These recoveries are not counted as original pre-edit captures. One event writer
repaired a real callback regression through its ordinary feature tests, before
its final pair; that is distinct from a preservation-pair benefit.

Some unchanged discovery commands ran larger suites after new test files were
added. Stable declared check bytes therefore did not establish an unchanged
complete executed suite. Several checks were copied or adapted by the writer;
no separately delegated preservation-check author was observed. Attribution and
chronology statements need the qualifications in the per-attempt audit, including
two attestations that mix selected existing files with writer additions elsewhere.

The final plugin guidance now explicitly requires retaining original receipts and
failed attempts, selecting preservation tests explicitly, and treating local
receipts as editable records. Those clarifications came after this frozen
comparison; their effect on future model behavior is unmeasured. The records
cannot authenticate authorship, prove adequate coverage or prevent a writer from
deleting its own evidence.

The earlier known-fixture smoke is separate from these 36 attempts. It completed
the mechanical pair and Forge workflow but failed external behavior and cleanup
review, and mislabeled writer-created checks as existing. Its original failed
result is retained. An earlier relative-runner invocation failed before launching
a writer; neither invocation is silently included in or excluded from this
registered comparison.

## Time and reported usage

All scheduled attempts count, including rejected work. Attempt time includes
preparation, writer execution and initial automated checks; independent review
and later review-application checks are excluded.

| Measure | Previous Forge | New Forge | Plain Codex |
|---|---:|---:|---:|
| Total attempt time | 4,015.609s | 5,695.775s | 1,378.075s |
| Seconds per accepted result | 502.0 | 712.0 | 153.1 |
| Uncached input tokens per accepted result | 113,156 | 149,724 | 32,226 |
| Cached input tokens per accepted result | 2,132,240 | 3,389,088 | 237,724 |
| Output tokens per accepted result | 23,435 | 33,358 | 7,298 |
| Monetary cost | Unknown | Unknown | Unknown |

Token figures are CLI-reported and rounded here; cached input is part of total
input. Subscription charges, reviewer costs and operator costs were not measured.
Host contention and service variability were uncontrolled. Extra time did not
produce a higher accepted-code total in this sample.

## Method and reproducibility

- Previous plugin: `594df8615e6b77f942e1d534facf82465344bcb2`, tree
  `bb37542242bb1ae07eddc8aa1a2f49d3f1e07744`.
- New plugin: `591440534182a2dd4bae48c250f31626ad8bde38`, tree
  `87e5ca5ae65e109c7b63aa584e053078935134e9`.
- One external evaluator: `4e7261c70e7f7334403de03a17b97de5247779f8`, tree
  `a6aada6fdd427d7bd927f7a870c8979c53dd7069`. All original source files and archive
  hashes were rechecked after execution; none changed.
- Implementation froze before independent evaluators authored the four task
  families. The evaluator, fixtures, prompts and acceptance rules froze before
  any comparative writer. Thirty-nine control tests cover four expected baseline
  failures, four good implementations, three legal alternatives and 28 rejected
  semantic mutants. These controls establish specific boundaries, not exhaustive
  coverage; the later review findings demonstrate that limit.
- Four constructed tasks × three fresh repetitions × three arms, standard depth,
  serial rotating order, shuffle seed 0. Every arm received the same task and
  cleanup/scope requirements. Repetitions are fresh workspaces, not retries.
- Codex CLI 0.154.0, requested `gpt-5.5` with medium effort; backend revision is
  unverified. macOS 26.6.2 arm64, Node v24.18.0. The evaluator used repository
  Python 3.12.13; shell `python3` was 3.14.7 and some writer commands selected it.
  Original freeze metadata recorded the shell version; this report distinguishes
  the evaluator interpreter.
- Three fresh independent reviewers saw shuffled baseline/candidate code, tests
  and automated check receipts. All twelve source-bound judgments were sealed
  before the separate trace audit. Blinding is partial because code can reveal
  workflow fingerprints; reviewer independence is an attestation.
- No operator candidate edits, corrective prompts, writer retries, acceptance
  changes or omitted failures. Writer command retries remain included; API retry
  counts are unknown. Final protected files and evaluator/plugin sources match.
  Observed commands showed no external checker/control access. Final-scope checks
  do not prove every transient write stayed in scope: writers removed misplaced
  planning artifacts in eleven attempts. This was not a syscall-level audit.

Repeat the registered setup from the frozen evaluator, then complete the
[independent review procedure](README.md#controlled-forgeplain-comparison):

```bash
python3 tools/trial_matrix.py compare /path/to/fresh-comparison \
  --plugin-root /path/to/5914405-snapshot \
  --previous-root /path/to/594df86-snapshot \
  --model gpt-5.5 --effort medium \
  --cases config-layers atomic-publish stream-records node-events \
  --depths standard --repeats 3 --timeout 900 --jobs 1 --seed 0
```

These are four constructed task families on one model setting and host, not 36
independent production tasks. Lean/deep generated-code results and native
Claude/Gemini model acceptance are not established here.

Later Windows invalid-option import handling, malformed-receipt error handling,
preservation guidance, documentation and inventory corrections do not change the frozen new-plugin arm.
Their regression tests verify those fixes; this experiment cannot measure their
effect on generated-code quality.
