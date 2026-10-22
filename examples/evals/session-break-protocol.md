# Session-break evaluation protocol

Status: draft protocol, not yet run. The full protocol needs the two-session
orchestrator described under [Harness support](#harness-support); a probe-free
manual pilot works with today's tools. Every run consumes model usage, so start
each pilot or main run only after the user approves its budget.

## Question

Is durable state and progress tracking Forge's core value? Earlier comparisons
cannot answer this. They were single-session, Codex-only and at `standard`
depth, so Forge's records were never needed:

| Evidence | Forge | Plain agent |
|---|---|---|
| [Behavior-contract trial](coding/behavior-contract-results-2026-10-02.md), accepted | 8/12 (previous and new) | 9/12 |
| [Quality trial](coding/quality-results-2026-10-02.md), accepted | 6/8 | 6/8 |
| Seconds per accepted result | 484–712 | 153–192 |
| Tokens per accepted result (uncached input, cache reads, output) | 2.3–14× plain | baseline |

The only interruption evidence is three Forge-only Codex runs that resumed a
prepared checkpoint after a 10-second kill, with no comparison arm and incomplete
usage. This protocol forces a break in the middle of multi-section work and asks
whether Forge's records reduce re-orientation cost and regressions enough to pay
for their overhead, and whether free-form notes do the same for less.

## Hypotheses and decision rule

- **H1, state matters:** both stateful arms beat `plain` on accepted outcome or
  regressions after the break.
- **H2, Forge's structure matters:** `forge-lean` beats `notes` on accepted
  outcome or regressions, at total tokens no worse than 1.5× `notes`.
- **H0, state is not the bottleneck:** all three arms finish alike; any
  difference is overhead.

Read results as follows:

- H2 holds: keep Forge's state machinery and cut what the evaluation shows as
  unused.
- `notes` matches `forge-lean` at lower cost: the benefit comes from durable
  notes, not Forge's assurance gates. Adopt the
  [state-first lean proposal](../../docs/proposals/state-first-lean.md) and rerun
  with it as a fourth arm.
- H0 holds: these tasks do not stress state. Report that and do not claim a
  state benefit.

With four repetitions per arm and case, a one-attempt difference is an
observation, not a statistically established effect.

## Arms

Every arm uses the same host, CLI version, model, effort, tools, timeouts and
task text. Claude writers now share `Read, Edit, Write, Bash, Glob, Grep, Skill,
Agent, TaskCreate, TaskGet, TaskList, TaskUpdate`; only `forge-lean` loads the
plugin. Standing instructions appear at the start of both session prompts, as a
repository instruction file would.

| Arm | Preparation | Standing instruction | Durable state |
|---|---|---|---|
| `forge-lean` | `coding_trials.py prepare --depth lean --plugin-root …` | The prepared Forge workflow text | `.planning/` plan, progress and receipts |
| `notes` | `coding_trials.py prepare --plain-agent` | "Keep `.planning/NOTES.md` as your durable working notes: the plan, each section's status, decisions and clarifications, verification results and the next step. Update it whenever one of these changes. When continuing work, read it before acting." | `.planning/NOTES.md` |
| `plain` | `coding_trials.py prepare --plain-agent` | none | whatever the agent leaves |

Notes live under `.planning/` because the checker admits only `src/`, `tests/`
and `.planning/`; a root `NOTES.md` would fail scope. A `plain` agent that
writes notes on its own keeps them; record that as observed behavior.

## Tasks and shared section plans

Use existing cases whose independent oracles have a `--compatibility-only`
mode for preserved behavior. Every arm receives the same five-section plan, so
decomposition is constant and only state differs.

**`godfile`** (81 oracle assertions):

1. Characterize the current public behavior of `dispatch` with regression tests:
   validation, totals and shipping, rendering and CSV/JSON, file access, and
   order transitions.
2. Move validation and money/shipping calculations into focused modules;
   `dispatch` keeps every public name.
3. Move rendering, serialization and file access into focused modules.
4. Move `OrderBook` workflow state and transitions into its own module.
5. Add `summarize_orders(orders)` and `OrderBook.summary()` with tests.

**`import-preview`** (638 assertions):

1. Characterize `import_orders`, pricing and receipts, including errors, with
   regression tests.
2. Consolidate the CSV parsing and validation duplicated in `orders` and
   `receipts` so the preview can reuse it; keep public behavior.
3. Add `preview_import` counts: `order_count`, `item_count` and `sku_quantities`.
4. Add `subtotal`, `tax` and `total`.
5. Match `import_orders` errors, keep inputs unchanged and handle header-only batches.

## Session break

1. **Session 1** prompt: standing instruction, the case prompt, the section plan,
   the session-1 clarifications below, and "In this session, complete sections 1
   and 2 only, then stop." The process exits or reaches its timeout.
2. **Checkpoint:** copy the workspace, run the case tests and the
   compatibility-only oracle against the copy, and keep session 1's prompt,
   events and telemetry.
3. **Session 2:** a fresh process with no `--resume` or `--continue`. Prompt:
   standing instruction, the case prompt, the section plan, and "An earlier
   session that you cannot see worked on this task in this workspace. Complete
   every remaining section and the task's acceptance requirements." It does not
   say which sections are done or repeat the clarifications.
4. **Host-switch variant:** session 2 runs on the other host with the same arm.
   Run it only when both CLIs are signed in; report it separately.

## Withheld-decision probe

Real work collects decisions in conversation that the brief never states. Each
case moves oracle-checked clauses out of the workspace brief and into the
session-1 prompt as the user's answers. Only durable state can carry them past
the break. The oracle stays unchanged.

| Case | Clause given only in session 1 | Brief after removal | Oracle evidence |
|---|---|---|---|
| `godfile` | `latest_created_on` covers every order, including cancelled ones | "latest normalized ISO date, or `None` when empty" | expects `2026-02-05`, the cancelled order's date |
| `godfile` | `status_counts` lists all four statuses, including zero counts | "counts by status"; the empty-input sentence also drops "zero counts" | checks zero counts after a transition and for an empty book |
| `import-preview` | Tax rounds down per line; never round again over the combined subtotal | "`subtotal`, `tax`, `total`: sums of the existing amounts" | compares against summed per-line reference amounts |

Before use, confirm that a candidate violating each clause fails the oracle and
that the good control still passes. Both cases implement these clauses after
the break (godfile section 5, import-preview section 4). If session 1 finishes
them early, record the overshoot.

## Metrics

| Metric | Definition |
|---|---|
| Accepted outcome | `coding_trials.py check --review` common-quality verdict: the same behavior, scope, oracle and cleanup rules for every arm. Both cases require cleanup, so every attempt needs an independent review blind to the arm; without one, cleanup fails. Report Forge workflow completion separately. |
| Total tokens | Session 1 plus session 2 `totals`. Claude `totals` come from the result's per-model usage, which includes subagents (`totals_source: modelUsage`); `top_level_totals` keep the result `usage`. Report uncached input, cache reads, cache writes and output separately, plus the CLI's cost estimate. |
| Re-orientation | Session-2 tokens and seconds before its first workspace edit (first `Edit`/`Write` tool call, or first Codex file change). Use main-loop assistant usage deduplicated by message ID. Subagent usage before that edit is not attributable. |
| Wall time | Runner seconds for both sessions, excluding harness checks and review. |
| Regressions | (a) the compatibility-only oracle passes at the checkpoint but fails at the end; (b) a test passing at the checkpoint fails, is deleted or loses assertions by the end; (c) the final oracle fails on a withheld clause. |
| Tool parity | `missing_tools` is empty in every Claude session. The CLI drops unknown or disabled tool names silently, so the init event is the only proof every arm had the same tools. |
| Secondary | Forge helper calls, `.planning/` size, whether session 2 read the state before its first edit, and session-1 overshoot or undershoot. |

Count every scheduled attempt, including failures and timeouts. Compute per
accepted figures the way `trial_matrix.py report` does.

## Procedure

**Preflight (no model calls).** Freeze an evaluator checkout and a plugin
snapshot. Confirm that native Claude installs and discovers all five skills
without Codex or user settings:

```bash
python3 /frozen/evaluator/tools/native_workflow_trials.py prepare /saved/claude-native --host claude \
  --plugin-root /frozen/plugin --claude-model YOUR_CLAUDE_MODEL --effort high
python3 /frozen/evaluator/tools/native_workflow_trials.py report /saved/claude-native
```

**Pilot (probe-free, available now).** One repetition per arm and case, with
the clauses left in the brief. This checks timeouts, prompts and metric
extraction. One arm on `godfile`:

```bash
python3 /frozen/evaluator/tools/coding_trials.py prepare /saved/break/godfile-1-notes --case godfile --plain-agent
# Write session-1.md and session-2.md beside prompt.md from the texts above.
cd /saved/break/godfile-1-notes/workspace
python3 /frozen/evaluator/tools/coding_trial_runner.py --host claude --model YOUR_CLAUDE_MODEL --effort high < ../session-1.md
mv ../telemetry.json ../session-1-telemetry.json && mv ../agent-events.jsonl ../session-1-events.jsonl
cp -R . ../checkpoint
python3 /frozen/evaluator/tools/godfile_trial_checks.py ../checkpoint godfile --compatibility-only
python3 /frozen/evaluator/tools/coding_trial_runner.py --host claude --model YOUR_CLAUDE_MODEL --effort high < ../session-2.md
mv ../telemetry.json ../session-2-telemetry.json && mv ../agent-events.jsonl ../session-2-events.jsonl
python3 /frozen/evaluator/tools/godfile_trial_checks.py . godfile --compatibility-only
python3 /frozen/evaluator/tools/coding_trials.py review-template /saved/break/godfile-1-notes > /saved/reviews/godfile-1-notes.json
# An independent reviewer, blind to the arm, completes the form from the frozen candidate.
python3 /frozen/evaluator/tools/coding_trials.py check /saved/break/godfile-1-notes --review /saved/reviews/godfile-1-notes.json
```

For `forge-lean`, prepare with `--depth lean --plugin-root /frozen/plugin`.
The runner then loads that plugin. Rename telemetry between sessions: the
runner overwrites `telemetry.json` and `agent-events.jsonl`. Keep review forms
outside the candidate workspace.

**Main run.** Two cases × three arms × four repetitions: 24 attempts, 48
sessions. Run serially and rotate arm order within each case-repetition block,
as `trial_matrix.py` schedules comparisons. Use the orchestrator so withheld
clauses, checkpoints and per-session evidence are uniform. Estimate the budget
from the pilot's measured usage and get approval first.

## Analysis

Report per arm and case: accepted count, total and per-accepted tokens, wall
time and re-orientation, plus regressions by type. Then apply the decision
rule. Have someone blind to the arms classify the oracle failure behind each
rejection: withheld clause, regression, unfinished work or other. Keep failed
and pending attempts in the denominator. Quote one session-2 opening per arm
to show how it re-oriented.

## Harness support

| Need | Status |
|---|---|
| Claude-only arms without Codex; `xhigh`/`max` effort; `Agent` and task tools | Available |
| Whole-tree Claude usage (`totals` with `totals_source`, `top_level_totals`, `subagent_stats`) and tool parity (`missing_tools`) | Available |
| Native Claude install and skill discovery in disposable settings | Available: `native_workflow_trials.py prepare --host claude` |
| Fresh second session with per-session telemetry | Partial: `coding_trials.py run --resume-runner` accepts only `--case resume`, kills on a timeout and sends a Forge-only continuation prompt |
| Any case and arm across a section-boundary break, with checkpoint copy and checks | Missing |
| Derived briefs for withheld clauses | Missing; `godfile_trial_checks.py` accepts only the case name `godfile` |
| Re-orientation offsets and regression diffs | Missing; derive them from retained events and checkpoints |
| Blind cleanup review across arms | Available for `trial_matrix.py` comparisons; the orchestrator must reuse its packets |

Proposed follow-up: `tools/session_break_trials.py` with `prepare`, `run` and
`report`. It would reuse `coding_trials.prepare`/`check`, the pinned writer
adapter and the existing oracles, and freeze evaluator and fixture hashes the
way existing matrices do.

## Threats to validity

- A clean break at a section boundary is gentler than a crash mid-edit. A
  time-based kill is a separate variant; arms reach different sections by a fixed
  time, so treat it as secondary.
- The `notes` instruction is itself a lightweight workflow. That is the point of
  the comparison; the plain arm remains the floor.
- Model and service variability, prompt caching and host contention affect
  tokens and time. Run serially and record CLI versions.
- Removing clauses changes what the brief says, not the oracle. Plain agents
  can still guess correctly, so probe failures are evidence, not certainty.
- Forge's planning text can show in the code, so blinding is partial.
