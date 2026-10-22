# Proposal: state-first `lean`

Status: built as an opt-in mode in 0.5.0 (`plan-setup --state-first`, described in
[state-first](../../skills/zagrosi-implement/references/state-first.md)). Defaults stay
unchanged until the user approves. Its finish also blocks while a section is not done.
Validate it with the [session-break protocol](../../examples/evals/session-break-protocol.md)
before adopting it.

## Summary

- `lean` would keep what a later session needs to continue: one plan file,
  per-section status, free-form notes and one final integration receipt bound to
  the tree.
- Assurance machinery moves to `standard`/`deep`, or to `lean` only when a
  risk trigger fires. That covers compatibility baseline/candidate pairs, red
  checkpoints, per-section receipts, plan lint, scoring, traceability gates and
  word or file budgets.
- Helper round trips drop from four to six per section, plus five, to about one
  per section plus three.

## Evidence

**Comparative trials.** These ran at `standard` depth, single-session and
Codex-only. Forge accepted 8/12 against 9/12 for the plain agent, and 6/8
against 6/8. It took 2.5–4.7× the seconds and 2.3–14× the tokens per accepted
result. No trial interrupted both arms, so the state benefit was never measured.

**First Claude-hosted run (0.4.1, lean, five fixes).** It needed eight plan
postflights (six when its friction log was written) and about 44 helper calls.
Lean budgets (eight owned files, 300-word sections, an 800-word plan) forced
five sections. Two full-suite runs of 23 minutes each dominated wall time. The
real defects came from the full suite and a fresh-context reviewer subagent, not
from per-section ceremony.

**This change (0.4.6, lean, four sections).** Forge built it, which gives a
second Claude data point:

- The plan was admitted on the third postflight. Two failures named gates
  ("needs a regression case and command") rather than fixes. Repairing them
  meant reading `planning_contract.py` and `markdown.py`. The fixes were an
  `Expected:` label instead of "Expected failure now:", named `test_*` cases,
  and not mixing inspection and test requirements in one contract.
- Sections with required compatibility checks took six calls: `implement-setup`
  with `--section`, baseline, `implement-progress` red, candidate, ordinary
  `implement-verify`, `implement-record-section`. The other sections took four.
  The run took more than 40 helper calls in all:
  - two were my own quoting errors and three were diagnostics;
  - two were integration runs I stopped so I could edit files. A receipt binds
    the whole tree, so any later edit, even to a document, needs a fresh
    full-suite run;
  - eight re-verified and re-recorded three sections after the review fixes.
- One regression surfaced: effort was validated before package safety. Existing
  preservation tests caught it. They would have caught it run directly; the
  receipt pair added bookkeeping, not detection.
- Two real hazards were found by ad hoc offline CLI probes, not by any gate:
  Codex reading `.claude-plugin` catalogs, and `TodoWrite` being off by default
  in Claude Code 2.1.304. Every gate then passed, and a fresh-context reviewer
  subagent still found three medium defects: an unrecorded Codex cache namespace
  left behind when Claude staging failed, a row lost to an unprepared resume
  host, and comparison token totals that left out subagents. It also found a
  protocol pilot that could never accept a cleanup-required task.
- No interruption occurred, so red checkpoints and receipts were written but
  never needed. Their value appears only after a break; the protocol measures it.
- At this point `status` reported the next section, changed inputs and the last
  red command, but `notes: null`. Decisions made in conversation survived only
  where I had written them into section evidence.

## What a resumed session needs

| Need | Current lean | State-first lean |
|---|---|---|
| Goal and acceptance | Unchanged brief plus a source-linked contract (`Source`, `Behavior`, `Expected`, `Command` per REQ) | `plan.md`: goal, acceptance list, optional REQ IDs |
| Sections, order, owned files | `sections/index.md` manifest plus one file per section | Section table in `plan.md` |
| Progress | Derived from records, receipts and hashed snapshots | `status.json`: each section `pending`, `active` or `done`, with files, host and time |
| Decisions and discoveries | Section evidence prose; optional progress notes | `notes.md`, append-only; each status change asks for an entry |
| Last verification | Per-section receipts and compatibility pairs | Last command and result line per section (agent-reported) |
| Completion proof | Final integration receipt and postflight | Unchanged: one captured integration receipt bound to the final tree |

The current records are built for tamper evidence: content hashes, origin
digests and receipt archives. A fresh session needs the next step and the
reasons behind earlier choices more than proof that bytes did not change.

## Proposed `lean`

1. `plan-setup` creates `plan.md`, `status.json` and `notes.md` from the brief
   and checks only structure: sections exist, owned files are listed, statuses
   are valid.
2. During work, one call per section marks it done, appends a note and
   optionally runs a targeted check: an extended `implement-progress --stage done`.
   Agents may also edit `notes.md` directly.
3. Finish captures the full integration suite and runs the structural
   postflight in one call: a merged `implement-verify` and `postflight`.
4. On resume, `status` prints the goal, each section's state, the latest notes,
   the last verification and files changed since the last status write.

Lean postflight blocks only on a missing or unparseable plan, a section without
a status, a missing or stale final receipt, or a failing final suite. Fresh
sessions start from `status`. Where the host supports subagents, run one
fresh-context review before finish instead of per-section review ceremony.

## What moves

| Mechanism | Proposed `lean` | `standard` / `deep` |
|---|---|---|
| Compatibility baseline/candidate pairs | Off; run existing tests before and after and note it | Required for refactors and interface changes |
| Red checkpoint (`implement-progress --stage red`) | Optional; note the failing test | Required |
| Per-section `implement-verify` receipts | Optional | Required |
| `lint-plan`, `lint-plan-artifacts`, `lint-sections`, `traceability`, `lint-implementation-readiness` | Off | On |
| `forge-score` | Off (compact plans already skip it) | On for physical plans |
| Word budgets and the eight-file section limit | Guidance only | As today |
| Source-linked REQ contract | Optional IDs | Required |

**Risk triggers** opt a lean section into the `standard` mechanism for that
section. They are: an explicit `risk:` tag in `plan.md`; a whole-word match of
the existing risk terms (security, privacy, auth, permission, migration, data,
payment, token, secret) in the section's goal or acceptance text; a change to
a published public interface; explicit REQ IDs in the brief; a configured team
handoff; or a user request for evidence. The plan itself stays lean.
`section_metrics` matches substrings today, so `auth` hits `author` in every
required compatibility block and `data` hits `metadata`. Reuse it only with
word boundaries and outside compatibility metadata.

## Fewer round trips

| Path | Today | Proposed |
|---|---|---|
| Plan | `plan-setup`, then `postflight` until it passes (3–8 times) | `plan-setup` (structure is checked on write) |
| Each section | `implement-setup` with `--section`, baseline, red, candidate, `implement-verify`, `implement-record-section` | One extended `implement-progress` call |
| Finish | `implement-verify`, `postflight` | One call |
| Resume | `status`, then section-specific actions | `status` |

Every failure message must name the missing field with an example line. For
instance: "REQ-005: add `Expected:` and `Command:`, or use
`verification_mode: inspection` with `Inspection:`, `Expected:` and a docs-only
`test_rationale:`."

## Compatibility

- Admitted compact and legacy plans keep today's semantics; their recorded
  depth decides. Nothing converts in place.
- Detached frozen execution and team reservations stay unchanged. Owned files
  stay per section, so reservations keep working.
- Promote a state-first plan by adding the `standard` contracts to it, not by
  rewriting it.
- The lean contract changes, so release this as a minor version.

## Rollout

1. Run the protocol's pilot and main run with today's `lean` (`forge-lean`,
   `notes`, `plain`).
2. Build state-first lean as an opt-in mode and rerun the protocol with it as a
   fourth arm.
3. Make it the default `lean` only if it accepts at least as many attempts as
   today's lean and the `notes` arm, has no more regressions, uses at most half
   of today's lean whole-tree tokens per accepted result, and needs at most
   N + 3 helper calls for N sections.
4. Otherwise keep today's `lean` and remove only the gates the evaluation shows
   unused.

## Decisions for the user

1. Budget for the protocol pilot (12 sessions) and main run (48 sessions).
2. Opt-in mode first (recommended) or replace `lean` directly.
3. Host-neutral names `plan.md`, `status.json` and `notes.md`, keeping legacy
   readers.
