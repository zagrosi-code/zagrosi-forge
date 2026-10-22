# Session-break results — 21 October 2026

**State-first `lean` did not clear its bar.** This rerun of the
[session-break protocol](session-break-protocol.md) added a fourth arm,
`forge-state`: Forge `lean` in the opt-in state-first mode, from the same runtime
as `forge-lean`. It used 0.57× the tokens and 0.56× the wall time of today's
`lean`, and all eight of its workflow records completed. It accepted 4/8 attempts,
against 5/8 for `forge-lean` and 4/8 each for `notes` and `plain`, and in one of
four attempts it lost a decision given only before the break. It missed all four
of the [proposal's](../../docs/proposals/state-first-lean.md) rollout criteria (H3),
so the protocol's reading is to keep today's `lean`. The first three arms gave the
first run's decision again: H1 holds and H2 does not. Four repetitions per arm and
case are observations, and no difference in accepted outcomes is distinguishable
from chance.

[Machine-readable results](session-break-results-2026-10-21.json) hold every
attempt of the main run and the pilot, the per-session usage, the checkpoint
checks, the probes and the decision inputs.

## Setup

- Protocol: [session-break protocol](session-break-protocol.md), run by
  `tools/session_break_trials.py` with `--probe` and
  `--arms forge-lean forge-state notes plain`. Three clauses left the briefs and
  reached session 1 only, as the user's answers.
- Evaluator: a frozen export of commit `66d841e`, tagged
  `eval/session-break-2026-10-21`. It was version 0.5.1 on its PR branch; main
  released this work as 0.5.2. That commit merges the session-break harness with
  the state-first mode and adds the fourth arm. The pilot ran `4dbdba1`, tagged
  `eval/session-break-2026-10-21-pilot`. A fresh-context review between the pilot
  and the main run led to the fixes in `66d841e`.
- Forge arms: both loaded one runtime-only snapshot of `66d841e` (`scripts/`,
  `skills/` and the plugin manifests). That tree carries today's `lean` and the
  opt-in state-first mode, so the two arms differed only in `forge-state`'s
  standing instruction, given at the start of both sessions: "Use Forge's opt-in
  state-first lean mode: pass `--state-first` to `plan-setup` and follow
  `<snapshot>/skills/zagrosi-implement/references/state-first.md`." The other
  three arms' prompts are unchanged from the
  [first run](session-break-results-2026-10-20.md). `forge-lean` therefore ran
  the code of `66d841e` here, against `bc6ee27` in the first run.
- Host: Codex CLI 0.154.0 with `gpt-5.6-terra` at medium effort for every arm, on
  macOS 26.6.2 arm64 and Python 3.14.7. Backend revisions are unverified.
- Schedule: two cases × four arms × four repetitions, so 32 attempts and 64
  sessions. Attempts ran serially, with arm order rotating within each
  case-repetition block. Each session had 1,800 seconds; none timed out or failed.
- Acceptance: the unchanged oracles plus an independent blind cleanup review.
  Eight fresh Claude Opus 5.5 reviewers each judged one case-repetition block from
  its packet alone, without arm labels. The reviewers shared one scratch
  directory, and one found a helper script of its own overwritten by another and
  redid its review in a private directory. Before the forms were applied, each was
  checked against its reviewer's own report; every form matched in verdicts,
  preferences and probe counts.
- Not run: the Claude arms and the host-switch variant. This run is Codex only.

## Results

| Arm | Case | Accepted | Withheld decision kept | Regressed attempts | Tokens | Uncached input + output | Wall s | Median s to first edit | Helper calls per attempt (median) |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| forge-lean | godfile | 2/4 | 4/4 | 0 | 13.86M | 0.97M | 3,248 | 87 | 27.5 |
| forge-lean | import-preview | 3/4 | 4/4 | 0 | 11.63M | 0.76M | 3,641 | 116 | 25.5 |
| forge-state | godfile | 2/4 | **3/4** | 1 (c) | 7.29M | 0.62M | 1,807 | 64 | 19.5 |
| forge-state | import-preview | 2/4 | 4/4 | 0 | 7.19M | 0.52M | 2,035 | 100 | 19.5 |
| notes | godfile | 2/4 | 4/4 | 1 (a) | 2.42M | 0.32M | 1,341 | 122 | 0 |
| notes | import-preview | 2/4 | 4/4 | 0 | 1.56M | 0.23M | 1,072 | 73 | 0 |
| plain | godfile | 0/4 | **0/4** | 4 (c) | 2.30M | 0.30M | 1,200 | 132 | 0 |
| plain | import-preview | 4/4 | 4/4 | 2 (b) | 1.32M | 0.18M | 892 | 57 | 0 |
| **forge-lean** | all | **5/8** | 8/8 | 0 | **25.49M** | 1.73M | 6,888 | 95 | 27.5 |
| **forge-state** | all | **4/8** | 7/8 | 1 | **14.48M** | 1.14M | 3,842 | 75 | 19.5 |
| **notes** | all | **4/8** | 8/8 | 1 | **3.99M** | 0.55M | 2,414 | 87 | 0 |
| **plain** | all | **4/8** | 4/8 | 6 | **3.62M** | 0.49M | 2,092 | 62 | 0 |

As in the first run, "Withheld decision kept" counts godfile's `status_counts`
clause (all four statuses, including zero counts) and import-preview's per-line
tax clause. Only the first separated the arms; per-line tax and godfile's
`latest_created_on` clause held in all 16 attempts of their case. Tokens are input
plus output over both sessions; cached input dominates. Per accepted result,
`forge-lean` used 5.10M tokens and 1,378 seconds, `forge-state` 3.62M and 960,
`notes` 1.00M and 603, and `plain` 0.91M and 523. Every scheduled attempt counts.

- **State-first cost less than today's `lean`, not less than notes.** It used
  14.48M tokens against 25.49M for `forge-lean`, and 3,842 against 6,888 wall
  seconds (exact Mann–Whitney p = 0.0011 for attempt tokens). Against `notes` it
  used 3.6× the tokens, 2.1× the uncached input and output, and 1.6× the wall time
  (p = 0.0002). At the break it kept six files and 6–9 KB under `.planning/` (plan,
  status, notes, spec, snapshot and lock), against 6–17 files and 6–52 KB for
  `forge-lean` and one notes file of at most 1.7 KB.
- **It carried the withheld decision in three of four attempts.** `forge-lean`
  and `notes` kept the `status_counts` clause 4/4 and `plain` 0/4, so the stateful
  arms together kept it 11/12 (Fisher p = 0.003 against plain). In the attempt
  that lost it, session 1 wrote only "summaries with the settled all-status and
  cancelled-date semantics" into `plan.md`'s acceptance list and nothing about it
  into `notes.md`. Session 2 then counted only the statuses it saw. The three attempts that kept
  the clause stated it ("all four status keys", "zero status counts include all
  four statuses", and a notes entry). The mode asks for decisions in `notes.md`,
  but nothing checks that they are stated.
- **Helper calls stayed far above the bar.** `forge-state` sessions ran a median
  of 19.5 helper commands per attempt (18–26), against the proposal's N + 3 = 8
  for five sections; `forge-lean` ran 27.5 (21–32). Counting each invocation inside
  chained commands, `forge-state` made a median of 22. Per attempt, that was about
  five `--stage started` and six `--stage done` calls, four `plan-setup` and four
  `status` runs, one or two `team status` checks and two to three red or baseline
  checkpoints. The state-first reference shows `started` before `done` for every
  section, so its own guidance already gives 2N + 3; repeated setup and status
  runs add the rest. One `forge-state` session 1 first ran `plan-setup` on the
  workspace's `prompt.md`, which put the state files at the workspace root, then
  moved to `.planning/` and removed them. The pilot showed the same detour; no
  attempt failed scope.
- **Outcomes did not separate.** Accepted counts were 5, 4, 4 and 4 of 8 (Fisher
  p = 1.0 for every pair). Regressed attempts were `forge-lean` 0, `forge-state` 1
  (the clause), `notes` 1 (a public import lost after a passing checkpoint) and
  `plain` 6 (four clauses, plus two import-preview attempts whose session 2
  deleted or renamed checkpoint tests). Plain against each other arm gives
  p = 0.007–0.041. Godfile's public-import compatibility caused six of the ten
  rejected godfile attempts, spread over every arm. The blind reviewers preferred `plain`
  in all four import-preview blocks (one tie with `forge-state`), and failed the
  cleanup of two import-preview attempts each from `forge-state` and `notes` and
  one from `forge-lean`. In godfile they preferred `notes` twice, `forge-state`
  once and `forge-lean` once.
- **Re-orientation was similar.** Session 2 reached its first `src/` or `tests/`
  change after a median of 95 seconds for `forge-lean`, 75 for `forge-state`, 87
  for `notes` and 62 for `plain` (pairwise p = 0.13–0.96). Before that edit the
  stateful arms ran a median of 7, 7.5 and 3 commands, and every one of their
  session 2s read `.planning/` first. Codex reports usage once per session, so
  tokens before the edit are unknown. Session-2 medians were 1.44M tokens for
  `forge-lean`, 1.05M for `forge-state` and 0.24M for `notes` and `plain`.
- **State-first records completed.** Its workflow verdict (plan, every section
  done, a fresh final receipt) passed in 8/8 attempts; `forge-lean`'s standard
  records passed in 6/8.
- **Sessions kept to their scope.** No session 1 started the post-break features:
  every checkpoint probe reported the feature missing. Four commands were flagged
  as reaching past the workspace: three `forge-lean` calls passed `--target-dir ..`
  from inside `.planning/`, which names the workspace itself, and one
  `forge-state` session ran `find .. -name AGENTS.md`, which found nothing. No
  session read another attempt or the evaluator.

## State-first against its bar

| Criterion (proposal, rollout step 3) | forge-state | Bar | Met |
|---|---|---|---|
| Accepted attempts | 4/8 | at least `forge-lean` (5/8) and `notes` (4/8) | no |
| Regressed attempts | 1 | no more than `forge-lean` (0) and `notes` (1) | no |
| Tokens per accepted result | 3.62M | at most half of `forge-lean`'s 5.10M | no (0.71×) |
| Helper calls, median finished attempt | 19.5 | at most N + 3 = 8 | no |

H3 fails on every criterion. H2 with `forge-state` in place of `forge-lean` also
fails: 4 against 4 accepted and 1 against 1 regressed attempts, at 3.6× the tokens
of `notes`. The two cost criteria miss by wide margins. The two outcome criteria
miss by one attempt each, which chance alone could produce.

## Decision rule

| Comparison | Accepted | Regressed attempts | Beats? |
|---|---|---|---|
| forge-lean vs plain | 5 vs 4 | 0 vs 6 | yes |
| notes vs plain | 4 vs 4 | 1 vs 6 | yes |
| forge-lean vs notes | 5 vs 4 | 0 vs 1 | yes, at 6.4× the tokens |
| forge-state vs plain | 4 vs 4 | 1 vs 6 | yes |
| forge-state vs notes | 4 vs 4 | 1 vs 1 | no |
| forge-state vs forge-lean | 4 vs 5 | 1 vs 0 | no; forge-lean beats forge-state |

H1 holds again: both stateful arms beat plain. H2 fails again, now on the token
bound alone: `forge-lean` beat `notes` by one accepted and one regressed attempt,
at 6.4× its tokens against the 1.5× bound. In the first run the same pair went
the other way (6 against 6 accepted, 1 against 0 regressed), so this ordering is
consistent with noise. H0 fails. The protocol's reading of H3: keep today's `lean`
and remove only the gates the evaluation shows unused.

These differences rest on small counts. Only the withheld-decision result, plain's
regressions and the token gaps are unlikely under chance alone. Every Forge token
comparison gives Mann–Whitney p ≤ 0.0011, while `notes` against `plain` gives
p = 0.38. Accepted outcomes differ by at most one attempt (p = 1.0). The tests were
chosen after the run and are not corrected for multiple comparisons. All results
come from one host, one model, one effort, two tasks and a clean break at a
section boundary.

## Pilot

The probe-free pilot (one repetition per arm and case, commit `4dbdba1`) checked
prompts, timeouts and metric extraction. All eight attempts ran without timeouts.
`forge-lean`, `forge-state` and `notes` were accepted 2/2 and `plain` 1/2 (godfile
compatibility). Tokens were 6.68M for `forge-lean`, 4.46M for `forge-state`,
1.21M for `notes` and 0.79M for `plain`. The pilot confirmed that the evaluator's
workflow check accepts a finished state-first plan, and it showed the root
`plan-setup` detour and 24–25 helper commands per `forge-state` attempt. A
fresh-context review of the harness then found three problems, fixed in `66d841e`
before the main run. The CLI default had started to include `forge-state`, which
would have added 16 unrequested sessions to the protocol's command. A `.git`
directory in a snapshot passed the probe guard. Cut-short sessions could pull the
helper-call median down.

## Limits

- This covers Codex only. Claude Code arms, the host switch and other models
  remain unmeasured.
- `forge-state`'s instruction is the only prompt difference from `forge-lean`.
  It names the mode and its reference but adds nothing about where to keep the
  brief or how to record decisions.
- `forge-lean` ran `66d841e`, which also changed risk-term matching on default
  paths, so it is not byte-identical to the first run's arm.
- After the run, a code review made state-first final receipts also cover
  `plan.md` and the brief, not only the source. Trial workspaces ignore
  `.planning/` in Git, so the receipts never covered those files there, and
  replaying the change leaves all eight `forge-state` workflow verdicts passing.
- Helper calls count commands that name `zagrosi_skills.py`, so a chained command
  counts once. The invocation counts above come from the session logs and are
  not part of the harness report.
- Godfile's public-import requirement and the import-preview cleanup review decided
  most rejections. Only one of three withheld clauses discriminated, so probe
  failures are evidence, not certainty.
- A clean break at a section boundary is gentler than a crash mid-edit.
- The blind reviewers are Claude subagents: independent of the Codex writers, but
  the same reviewer family for every arm, and they shared a scratch directory.
  Independence is an attestation, and code can reveal its workflow, so blinding is
  partial.
- Wall time includes model-service variability; attempts ran serially on one
  machine.
- Raw evidence (events, telemetry, checkpoints, blind packets and review forms)
  is kept locally under the ignored `docs/development/` folder, not in Git.

## Reproduce

```bash
git fetch origin tag eval/session-break-2026-10-21
mkdir -p /frozen/evaluator /frozen/plugin
git archive 66d841e | tar -x -C /frozen/evaluator
git archive 66d841e scripts skills .codex-plugin .claude-plugin | tar -x -C /frozen/plugin
python3 /frozen/evaluator/tools/session_break_trials.py prepare /saved/main-codex --repeats 4 --probe \
  --arms forge-lean forge-state notes plain --host codex --model gpt-5.6-terra --effort medium \
  --plugin-root /frozen/plugin --state-plugin-root /frozen/plugin --timeout 1800 --seed 20261022
python3 /frozen/evaluator/tools/session_break_trials.py run /saved/main-codex
python3 /frozen/evaluator/tools/session_break_trials.py blind /saved/main-codex
# Reviewers blind to the arms complete every blind/*/review.json from the packets alone.
python3 /frozen/evaluator/tools/session_break_trials.py apply-reviews /saved/main-codex
python3 /frozen/evaluator/tools/session_break_trials.py report /saved/main-codex
```

A requested model alias does not guarantee exact repetition.
