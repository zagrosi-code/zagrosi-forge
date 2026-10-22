# Session-break results, Claude Code — 21 October 2026 (partial)

**Partial run; no acceptance verdicts.** The first Claude Code run of the
[session-break protocol](session-break-protocol.md) stopped at the user's request
after two of four repetitions: twelve attempts completed, one was interrupted and
eleven never ran. Blind cleanup review never ran, so no attempt is accepted and
the decision rule does not apply. The figures below are mechanical outcomes only:
oracle, probes, regressions, timeouts, usage and time, with two attempts per arm
and case.

- **Forge `lean` ran out of room at max effort.** No session 1 reached code: all
  four spent the time before the break on planning (12–14 `.planning/` files).
  Three of four attempts ended with features unfinished. Two of those session 2s
  ended their turn to wait for a background subagent, and print mode exits when
  the main loop does, so the delegated work was lost. The third timed out. Only
  Forge used subagents.
- **Plain agents kept notes on their own.** All four `plain` session 1s wrote a
  `.planning/` file that held the withheld decision, so `notes` and `plain` both
  kept 4/4 decisions and the probe could not separate them. Codex `plain` agents
  kept a notes file in 1 of 8 attempts, and it lacked the answer (Fisher p = 0.01).
- **Godfile compatibility failed for every `notes` and `plain` attempt.** Both
  `plain` attempts dropped public imports (`date`, then `Path`), the failure that
  dominated the Codex runs. Both `notes` attempts turned `dispatch.py` into a
  package, which the oracle rejects because it requires `src/dispatch.py`.

[Machine-readable results](session-break-results-2026-10-21-claude.json) hold
every scheduled attempt of the main run and the pilot, with per-session usage.

## Setup

- Protocol: `tools/session_break_trials.py` with `--probe`, arms `forge-lean`,
  `notes` and `plain`, seed 20261022, 1,800 seconds per session.
- Host: Claude Code 2.1.304 with `claude-opus-5-5` at `max` effort for every
  arm, the user's choice, on macOS 26.6.2 arm64 and Python 3.14.7.
- Evaluator: a frozen export of `a8da00a`, tagged
  `eval/session-break-2026-10-21-claude`, the pre-merge form of release 0.5.3's
  harness changes. The main report was recomputed with release 0.5.3's report
  code, which fixes the per-arm observed input total. The Forge arm loaded the
  same runtime-only snapshot of `bc6ee27` as the first Codex run.
- Isolation: no settings sources, no MCP servers, auto-memory off, and none of
  the launching session's 35 `CLAUDE*`/`ANTHROPIC_*` variables. Every session
  registered all twelve tools (`missing_tools` empty). No `memory/` folder
  appeared under `~/.claude/projects` for any workspace. Claude Code still kept
  subagent metadata and large tool outputs there for Forge sessions; no session
  read them.
- Pacing: one attempt at a time. Between attempts, the driver waited for the
  five-hour usage window to reset whenever it reached 0.75 (0.65 after the first
  pause).
- Not run: blind review, repetitions 3–4 and the host-switch matrix
  (Claude session 1, Codex session 2).

## Results

| Arm | Case | Oracle passed | Decision kept | Regressed | Session timeouts | Observed input | Wall s | Session-2 s to first edit |
|---|---|---:|---|---:|---:|---:|---:|---|
| forge-lean | godfile | 0/2 | unobserved (feature missing) | 0 | 2/4 | 24.91M | 6,386 | 1,241, 801 |
| forge-lean | import-preview | 1/2 | 1/1 (1 missing) | 0 | 2/4 | 18.53M | 5,925 | 667, none |
| notes | godfile | 0/2 | 2/2 | 1 | 0/4 | 14.43M | 4,517 | 427, 532 |
| notes | import-preview | 2/2 | 2/2 | 0 | 0/4 | 5.40M | 2,266 | 157, 228 |
| plain | godfile | 0/2 | 2/2 | 2 | 0/4 | 12.60M | 4,352 | 354, 480 |
| plain | import-preview | 2/2 | 2/2 | 1 | 0/4 | 5.25M | 2,331 | 291, 363 |
| **forge-lean** | all | **1/4** | 1/1 | 0 | 4/8 | **43.43M** | 12,311 | median 801 (n = 3) |
| **notes** | all | **2/4** | 4/4 | 1 | 0/8 | **19.83M** | 6,783 | median 328 |
| **plain** | all | **2/4** | 4/4 | 3 | 0/8 | **17.85M** | 6,683 | median 358 |

"Oracle passed" is the unchanged oracle on the final tree, not acceptance.
"Decision kept" counts the discriminating clause of each case: godfile's
`status_counts` zero counts and import-preview's per-line tax. Observed input is
uncached input plus cache reads and writes over both sessions, subagents
included, taken from the session logs. It matches the CLI's totals exactly in 17
of the 20 sessions that reported them; the three Forge sessions with subagents
differ by −2% to +8%. It is a lower bound for sessions cut off at 1,800 seconds,
and Claude logs carry no final output counts. Output made up 3.7% of `notes` and
4.1% of `plain` totals. The CLI's cost estimates, which are not subscription
charges, were $26.92 for `notes` and $26.05 for `plain`. Forge's four sessions
that ended on their own cost $20.79; its four timed-out sessions report no cost.

- **Forge `lean` spent its time on process.** Forge's input was 2.2× that of
  `notes` (exact Mann–Whitney p = 0.11 over four attempts each). Its wall time was
  1.8× (p = 0.03), on less finished work. Forge made 13–34 helper calls per
  attempt. Session 2 reached its first code edit after a median of 801 seconds,
  against 328 for `notes` and 358 for `plain`. The one complete Forge attempt
  (import-preview, repetition 1) finished all five sections in session 2 and
  recorded each one, after a session 1 that had stopped at section 1.
- **The break held its sections everywhere else.** Every `notes` and `plain`
  session 1 ended on its own with code and tests changed at the checkpoint, and
  none started a post-break feature (every checkpoint probe reported the feature
  missing).
- **Regressions were deleted or renamed tests.** `plain` lost a checkpoint test
  in all three of its regressed attempts. The protocol counts a renamed test as
  deleted. `notes` had one checkpoint test fail at the end. Compatibility never
  went from passing at the checkpoint to failing at the end: godfile
  compatibility already failed at every `notes` and `plain` checkpoint.
- **Forge's oracle failures are unfinished work, not regressions.** Forge kept
  compatibility in all four attempts, but it built less. The same oracle cannot
  tell care from incompleteness here.

## Against the Codex runs

The first [Codex run](session-break-results-2026-10-20.md) used `gpt-5.6-terra`
at medium effort; for the same two repetitions:

| Arm | Oracle passed (Codex / Claude) | Decision kept | Regressed | Input tokens | Wall s |
|---|---|---|---|---|---|
| forge-lean | 2/4 / 1/4 | 4/4 / 1/1 | 1 / 0 | 13.41M / 43.43M | 3,314 / 12,311 |
| notes | 3/4 / 2/4 | 4/4 / 4/4 | 0 / 1 | 2.11M / 19.83M | 936 / 6,783 |
| plain | 2/4 / 2/4 | 2/4 / 4/4 | 2 / 3 | 1.96M / 17.85M | 775 / 6,683 |

Model, effort, host and tokenizer all differ, so these are side-by-side
observations, not a host comparison. Two contrasts are larger than the noise:
Claude used 3–9× the input and 4–9× the wall time at max effort, and Claude's
`plain` agents wrote durable notes that Codex's did not. On Claude, `plain` is
therefore not a stateless floor, and H1 needs a control that keeps no notes,
such as `plain` with `.planning/` outside scope.

## Pilot and harness fixes

The probe-free pilot ran two of six attempts on evaluator `21d78e4`, tagged
`eval/session-break-2026-10-21-claude-pilot`, before it was stopped in favour of
the main run. Godfile `forge-lean` timed out in both sessions yet passed the
oracle and both probes. Godfile `notes` finished in 33 minutes, then dropped the
public import `Path`. The pilot led to the fixes in release 0.5.3:
- Writers start without the launching Claude Code session's environment and with
  auto-memory off.
- The Forge arms may read their plugin tree; `--restricted` had denied three
  reads per session.
- Reports count observed input from the logs, because a timed-out session
  reports no usage.

## Limits

- Twelve of 24 attempts, two per arm and case, with no cleanup review: no
  acceptance, no decision rule and no claim about H0–H2.
- One model at one effort. At max effort, Forge's sessions hit the 1,800-second
  limit, so its results mix the protocol's clean break with time-based kills.
- The interrupted attempt (godfile `notes`, repetition 3) ran while the Mac
  slept with its lid closed. The harness's monotonic timeouts did not count that
  time, so its sessions ran 47 and 101 minutes of wall time. It is excluded from
  every figure.
- Raw evidence (events, telemetry, checkpoints) stays in the ignored
  `docs/development/` folder, not in Git.

## Reproduce

```bash
git fetch origin tag eval/session-break-2026-10-21-claude
mkdir -p /frozen/evaluator /frozen/plugin
git archive a8da00a | tar -x -C /frozen/evaluator
git archive bc6ee27 scripts skills .codex-plugin .claude-plugin | tar -x -C /frozen/plugin
python3 /frozen/evaluator/tools/session_break_trials.py prepare /saved/main-claude --repeats 4 --probe \
  --host claude --model claude-opus-5-5 --effort max --plugin-root /frozen/plugin --timeout 1800 --seed 20261022
python3 /frozen/evaluator/tools/session_break_trials.py run /saved/main-claude
python3 /frozen/evaluator/tools/session_break_trials.py report /saved/main-claude
```

Run attempts one at a time and keep the account's usage windows below their caps,
because a session refused at a cap counts as a failed attempt.
