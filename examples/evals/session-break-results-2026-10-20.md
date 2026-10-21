# Session-break results — 20 October 2026

**State mattered; Forge's structure did not.** This run forced a break after
section 2 of five. Both stateful arms kept a decision that was given only before
the break, and the plain agent lost it in all four attempts where it mattered.
Free-form notes matched Forge `lean` on accepted outcomes (6/8 each against 4/8
for plain), with fewer regressed attempts (0 against 1). They cost one sixth of
Forge's tokens and one third of its wall time. Under the protocol's decision rule
the data support **H1** and not **H2**. The rule's next step is to adopt the
[state-first `lean` proposal](../../docs/proposals/state-first-lean.md) and rerun
it as a fourth arm. Four repetitions per arm and case are observations, not
established effects.

[Machine-readable results](session-break-results-2026-10-20.json) hold every
attempt of the main run and the pilot, the per-session usage, the checkpoint
checks, the probes and the decision inputs.

## Setup

- Protocol: [session-break protocol](session-break-protocol.md), run by
  `tools/session_break_trials.py`. The main run used `--probe`: three clauses
  left the briefs and reached session 1 only, as the user's answers.
- Evaluator: a frozen export of commit `bc6ee27` (release 0.4.4). The Forge arm
  received a runtime-only snapshot of the same commit (`scripts/`, `skills/` and
  the plugin manifests), so its plugin tree held no copy of the withheld clauses.
- Host: Codex CLI 0.154.0 with `gpt-5.6-terra` at medium effort for every arm,
  on macOS 26.6.2 arm64 and Python 3.14.7. Earlier trials requested `gpt-5.5`,
  which Codex no longer accepts with a ChatGPT account. Backend revisions are
  unverified.
- Schedule: two cases × three arms × four repetitions, so 24 attempts and 48
  sessions. Attempts ran serially, with arm order rotating within each
  case-repetition block. Each session had 1,800 seconds; none timed out or
  failed.
- Acceptance: the unchanged oracles plus an independent blind cleanup review.
  Eight fresh Claude Opus 5.5 reviewers each judged one case-repetition block
  from its packet alone (source, tests, check results), without arm labels.
- Not run: the Claude arms, because the standalone `claude` CLI was not signed
  in (`claude auth status` reported `loggedIn: false`). The host-switch variant
  was skipped for the same reason.

## Results

| Arm | Case | Accepted | Withheld decision kept | Regressed attempts | Tokens | Uncached input + output | Wall s | Median s to first edit |
|---|---|---:|---|---:|---:|---:|---:|---:|
| forge-lean | godfile | 2/4 | 4/4 | 1 (a) | 15.05M | 1.03M | 3,522 | 81 |
| forge-lean | import-preview | 4/4 | 4/4 | 0 | 12.00M | 0.85M | 2,416 | 76 |
| notes | godfile | 2/4 | 4/4 | 0 | 2.85M | 0.36M | 1,246 | 113 |
| notes | import-preview | 4/4 | 4/4 | 0 | 1.45M | 0.19M | 592 | 47 |
| plain | godfile | 0/4 | **0/4** | 4 (c) | 2.32M | 0.32M | 971 | 98 |
| plain | import-preview | 4/4 | 4/4 | 0 | 1.43M | 0.20M | 620 | 33 |
| **forge-lean** | all | **6/8** | 8/8 | 1 | **27.05M** | 1.88M | 5,939 | 80 |
| **notes** | all | **6/8** | 8/8 | 0 | **4.30M** | 0.55M | 1,838 | 80 |
| **plain** | all | **4/8** | 4/8 | 4 | **3.76M** | 0.52M | 1,591 | 43 |

"Withheld decision kept" counts godfile's `status_counts` clause (all four
statuses, including zero counts) and import-preview's per-line tax clause. Only
the first separated the arms. Per-line tax, and godfile's third clause
(`latest_created_on` covering cancelled orders), match what every agent did
anyway, so all twelve attempts of each case kept them. Tokens are input plus
output over both sessions; cached input dominates. Per accepted result, Forge
used 4.51M tokens and 990 seconds, notes 0.72M and 306 seconds, and plain 0.94M
and 398 seconds. Every scheduled attempt counts.

- **State carried the decision.** Both stateful arms wrote the answer into
  durable state before the break; notes said, for example, "all four status keys
  are present". Session 2 then implemented it in all eight godfile attempts. The
  plain agents kept no notes, with one exception: a 276-byte file that lacked the
  answer. All four built `status_counts` from the statuses they saw. Fisher's
  exact test gives p = 0.002 for stateful (8/8) against plain (0/4), and
  p = 0.029 for each stateful arm alone.
- **Forge's structure added no observed benefit.** Forge and notes accepted the
  same count (Fisher p = 1.0). Forge's one regression was a session 2 that
  dropped a public `dispatch` import while finishing the remaining extraction.
  Forge's own completion records passed in 6 of 8 attempts. Blind reviewers
  preferred notes in four of eight blocks, plain in two and Forge in one, and
  gave no preference in one.
- **Forge cost far more.** It used 6.3× the tokens of notes and 7.2× those of
  plain. Counting only uncached input and output, it used 3.4×, and it took 3.2×
  the wall time of notes. Every Forge attempt used more tokens than every notes
  attempt (exact Mann–Whitney p ≈ 0.0002). Notes and plain did not differ
  (p = 0.72). Forge kept 10–17 files and 14–47 KB under `.planning/` at the
  break, against one NOTES.md of 0.8–1.8 KB. Its sessions made 27–36 helper
  calls per attempt.
- **Re-orientation was not faster with Forge.** Session 2 reached its first
  `src/` or `tests/` change after a median of 80 seconds for Forge and for
  notes, and 43 seconds for plain (pairwise p = 0.16–0.51). Before that edit
  Forge ran a median of 7 commands, notes 2.5 and plain 3. Codex reports usage
  once per session, so tokens before the first edit are unknown. Session-2
  medians were 1.48M tokens for Forge, 0.26M for notes and 0.25M for plain.
- **Public imports dominated godfile failures in every arm.** Eight of twelve
  session 1s removed a public name such as `dispatch.date` while extracting
  validation. Session 2 restored it in four of those eight and broke it once
  more after a passing checkpoint (the Forge regression). The final
  compatibility pass rates were Forge 2/4, notes 2/4 and plain 3/4, so state did
  not help here.
- **Sessions kept to their scope.** No session 1 started the post-break
  features: every checkpoint probe reported the feature missing. No session read
  another attempt or the evaluator. One notes session opened a user-level Codex
  skill (`~/.agents/skills/context7-mcp/SKILL.md`), which Codex still exposes
  under `--ignore-user-config`. Every arm had the same exposure, and the skill
  holds no trial data.

## Decision rule

| Comparison | Accepted | Regressed attempts | Beats? |
|---|---|---|---|
| forge-lean vs plain | 6 vs 4 | 1 vs 4 | yes |
| notes vs plain | 6 vs 4 | 0 vs 4 | yes |
| forge-lean vs notes | 6 vs 6 | 1 vs 0 | no; notes beats forge-lean |

H1 holds: both stateful arms beat plain. H2 fails: Forge does not beat notes,
and its tokens are 6.3× those of notes against the 1.5× bound. H0 fails: arms
differ. The protocol's reading: notes match Forge at lower cost, so the benefit
comes from durable notes, not Forge's assurance gates.

These differences rest on small counts. Only the withheld-decision result and
the cost gap are unlikely under chance alone: accepted outcomes differ by two
attempts (Fisher p = 0.61), and regressed attempts give p = 0.077 for notes
against plain. The tests were chosen after the run and are not corrected for
the three probes. All results come from one host, one model, one effort, two
tasks and a clean break at a section boundary.

## Pilot

The probe-free pilot (one repetition per arm and case, commit `e7460bf`) checked
prompts, timeouts and metric extraction. All six attempts ran without timeouts.
Import-preview was accepted in every arm, and godfile failed public-import
compatibility in every arm. Forge used 4.96M tokens against 0.91M for notes and
0.93M for plain. An independent review of the harness after the pilot led to the
fixes in `bc6ee27` before the main run. Missing observations now stay unknown,
every other attempt is hidden during session 2, a failed session 1 ends its
attempt, and the Forge arm under `--probe` receives a runtime-only plugin tree.

## Limits

- This covers Codex only. Claude Code arms, the host switch and other models
  remain unmeasured.
- Godfile's public-import requirement caused most failures regardless of arm.
  Only one of three withheld clauses discriminated, so probe failures are
  evidence, not certainty.
- A clean break at a section boundary is gentler than a crash mid-edit.
- The blind reviewers are Claude subagents: independent of the Codex writers,
  but the same reviewer family for every arm. Independence is an attestation,
  and code can reveal its workflow, so blinding is partial.
- Wall time includes model-service variability; attempts ran serially on one
  machine.
- Raw evidence (events, telemetry, checkpoints, blind packets and review forms)
  is kept locally under the ignored `docs/development/` folder, not in Git.

## Reproduce

```bash
mkdir -p /frozen/evaluator /frozen/plugin
git archive bc6ee27 | tar -x -C /frozen/evaluator
git archive bc6ee27 scripts skills .codex-plugin .claude-plugin | tar -x -C /frozen/plugin
python3 /frozen/evaluator/tools/session_break_trials.py prepare /saved/main-codex --repeats 4 --probe \
  --host codex --model gpt-5.6-terra --effort medium --plugin-root /frozen/plugin --timeout 1800 --seed 20261021
python3 /frozen/evaluator/tools/session_break_trials.py run /saved/main-codex
python3 /frozen/evaluator/tools/session_break_trials.py blind /saved/main-codex
# Reviewers blind to the arms complete every blind/*/review.json from the packets alone.
python3 /frozen/evaluator/tools/session_break_trials.py apply-reviews /saved/main-codex
python3 /frozen/evaluator/tools/session_break_trials.py report /saved/main-codex
```

A requested model alias does not guarantee exact repetition.
