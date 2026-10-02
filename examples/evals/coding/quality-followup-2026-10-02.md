# Quality follow-up — 2 October 2026

**All three arms accepted 2 of 4 attempts. This follow-up does not demonstrate
better generated-code reliability.** Independent review preferred previous Forge
in one comparison, current Forge in one, plain Codex in one, and found no acceptable
candidate in the fourth. Plain Codex used less time and fewer tokens.

[Machine-readable results](quality-followup-2026-10-02.json) retain all twelve
attempts, source identities, check results, reviewer judgments and usage. The
[earlier experiment](quality-results-2026-10-02.md) remains unchanged.

## Acceptance and independent review

Accepted completion requires passing behavior, scope, source identity, meaningful
cleanup review and runner completion; Forge also requires workflow completion.
All twelve writers finished without timeout and passed their own tests. All eight
Forge attempts completed their workflows. Neither observation proves correctness.

| Task | Previous Forge accepted | Current Forge accepted | Plain accepted | Preferred, repetitions 1 / 2 |
|---|---:|---:|---:|---|
| Summary feature and encountered cleanup | 0/2 | 0/2 | 1/2 | Plain / none acceptable |
| Order-dispatch module extraction | 2/2 | 2/2 | 1/2 | Previous / current |
| **Total** | **2/4** | **2/4** | **2/4** | **One preference each; one rejected group** |

The frozen automated behavior checks passed 4/4 previous, 2/4 current and 2/4 plain.
Independent review then rejected both previous summary candidates for additional
compatibility regressions: eager quantity counting changed legacy behavior for a
valid integer subclass; set-based action dispatch changed an unknown list or
dictionary action from `ValueError` to `TypeError`. The original passing check
receipts remain visible beside those failing reviews.

Both current summary candidates changed missing-field error behavior. The second
plain summary changed observable input access/consumption; the second plain
godfile omitted the public `Path` import. Useful cleanup cannot excuse those breaks.

Reviewers found meaningful responsibility cleanup in all six godfile candidates.
The first preferred previous Forge's separation of filesystem access and strong
compatibility tests. The second preferred current Forge's direct summary path,
avoiding repeated copying, sorting and validation. Every review form validated;
no candidate or judgment was changed to obtain a pass.

## Cost of accepted work

Every attempt counts, including rejected candidates. Time includes preparation,
writer execution and initial automated checks; independent review and subsequent
review-application checks are excluded.

| Measure | Previous Forge | Current Forge | Plain |
|---|---:|---:|---:|
| Total attempt time | 1,468.895s | 1,447.728s | 760.738s |
| Seconds per accepted result | 734.4 | 723.9 | 380.4 |
| Uncached input tokens per accepted result | 131,496 | 143,627 | 49,551 |
| Cached input tokens per accepted result | 2,169,344 | 2,410,496 | 558,528 |
| Output tokens per accepted result | 33,636 | 33,690 | 18,413 |
| Financial cost | Unknown | Unknown | Unknown |

Tokens are CLI-reported and rounded here; cached input is part of total input.
Token counts are not subscription charges. With four attempts per arm, these are
observations rather than a general cost or speed result.

## Method and source identity

- Previous Forge: `2b7b22bbf7207a6bd7add656bdada435c748d09e`, tree
  `782d2fb1f1c8fb3f1088bec6d97a04780ee50877` (PR21 before this follow-up).
- Current Forge and evaluator: `fe85ea1fb883ed606a7fff2a9d9c9ff883c00353`, tree
  `9bba389dccbc09026089aa2869af2417db9fdfd0`. Frozen file hashes were rechecked
  before publication. Later documentation and timeout-test corrections do not
  change these snapshots.
- Codex CLI 0.154.0, requested `gpt-5.5` with medium effort; macOS 26.6.2 arm64,
  Python 3.14.7. Backend model revision is unverified.
- Two tasks, two fresh repetitions, three arms, standard depth. Serial execution,
  rotating arm order, 900-second deadline per writer, shuffled review seed 2102.
  All arms use the same immutable evaluator and isolated Git baseline.
- Every writer receives the same explicit requirement for useful cleanup in the
  changed execution path. Historical prompts did not disclose that acceptance
  condition equally. This is a new experiment; do not pool its rates with the
  [earlier observations](quality-results-2026-10-02.md).
- Two reviewers who did not write these candidates inspected shuffled source,
  tests and check receipts without arm identities, logs, time or usage. They
  previously reviewed Forge or earlier trials. Blinding is partial; independence
  is an attestation. Supplied receipts do not prove when tests were written.
- No operator candidate edits, corrective prompts, reruns or omitted failures.
  Model command retries remain included; API retry counts are unknown. Some
  local checks overlapped execution, so time includes uncontrolled host contention
  and service variability. Claude/Gemini and native cross-host model acceptance
  remain unavailable.

To repeat the configuration from the frozen evaluator:

```bash
python3 tools/trial_matrix.py compare /path/to/fresh-comparison \
  --plugin-root /path/to/fe85ea1-snapshot \
  --previous-root /path/to/2b7b22b-snapshot \
  --model gpt-5.5 --effort medium --cases summary godfile \
  --depths standard --repeats 2 --timeout 900 --seed 2102
```

Prepare and complete independent blinded reviews using the
[trial guide](README.md#controlled-forgeplain-comparison). A model alias does not
guarantee identical future results.

## What the changes establish

Targeted regressions verify that compact handoffs preserve original source
promises, including their Markdown boundaries; incomplete or stale review
evidence is rejected; and the invoice oracle catches ten previously accepted
compatibility mutants while permitting valid cleanups. These are concrete Forge
correctness improvements. They do not establish that its generated code is
generally better.

Both current summary candidates passed their own tests and Forge completion,
but the independent oracle caught changed errors for an empty item mapping.
The second plain summary candidate changed observable input access/consumption.
The second plain godfile candidate omitted the public `Path` import. All four
failed candidates passed their own tests. No candidate was repaired. These failures show why completion records and
successful candidate tests cannot replace independent caller checks.

The two additional regressions discovered by blinded review are now covered by
the current oracle for future evaluations (four new mutation regressions). This report uses the original frozen
oracle throughout; its false greens are preserved rather than rewritten.

The practical conclusion is narrower than the code-quality goal: source handoffs
and evidence checks are stronger, but guidance and workflow completion still do
not reliably prevent compatibility mistakes. Independent caller checks and code
review remain necessary. A broader held-out evaluation is needed before claiming
that additional Forge process produces better code.
