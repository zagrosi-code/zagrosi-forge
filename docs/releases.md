# Releases

## 0.5.2 — state-first arm for session-break trials

- `tools/session_break_trials.py` adds a fourth arm, `forge-state`: Forge `lean` in the opt-in state-first mode, loaded from its own frozen snapshot (`--state-plugin-root`, which defaults to `--plugin-root`). Its standing instruction names the mode; the other arms' prompts are unchanged.
- Reports count helper calls per attempt against N + 3 for N sections. With `forge-state` present, the decision rule adds `state_first`: H2 with `forge-state` in place of `forge-lean`, and H3, the state-first proposal's rollout bar against `forge-lean` and `notes`. H0–H2 still compare the first three arms, so the first run's decision reproduces unchanged.
- A state-first implement postflight now reports `sections_recorded_complete`, so trial and eval readers count a finished state-first plan as complete.
- `--arms` still defaults to the first three arms. Under `--probe`, plugin snapshots may not hold `.git`, and every run rechecks them. A session without logged events leaves helper calls unknown.
- Second [Codex results](../examples/evals/session-break-results-2026-10-21.md): state-first `lean` used 0.57× the tokens of today's `lean` but accepted 4/8 against 5/8, lost one withheld decision and ran a median of 19.5 helper commands against a bar of 8, so it missed all four rollout criteria. The first three arms repeated H1 true and H2 false.

## 0.5.1 — session-break trials

- Run the [session-break protocol](../examples/evals/session-break-protocol.md) end to end with `tools/session_break_trials.py`: any arm through a forced break after section 2, a checkpoint with per-test, oracle and clause-probe results, and a fresh second session that cannot read the first session's files. Reports add re-orientation time, regressions against the checkpoint and blind review through the existing comparison packets.
- `--probe` removes the withheld decision clauses from each brief before the baseline is recorded and gives them in session 1 only. Tests confirm that violating each clause fails the unchanged oracle while a good control passes.
- Claude writers no longer save session transcripts (`--no-session-persistence`), so a later session cannot find an earlier one on disk.
- First [Codex results](../examples/evals/session-break-results-2026-10-20.md): both stateful arms kept a pre-break decision that plain agents lost in 4 of 4 attempts. Notes matched Forge `lean` (6/8 accepted each, plain 4/8) at a sixth of its tokens, which supports H1 but not H2.

Runtime code is unchanged apart from regenerated manifests.

## 0.5.0 — opt-in state-first lean

- `plan-setup --state-first` keeps a lean plan as `plan.md` (goal, acceptance and a section table), `status.json` (each section pending, active or done, with files, host and time) and append-only `notes.md`. Setup drafts `plan.md` from the brief and checks only structure; every failure names the field with an example.
- `implement-progress --stage done --notes ... [-- <check>]` finishes a section in one call. `implement-verify -- <full suite>` captures the final receipt and runs the structural postflight, which blocks only on the plan, section statuses and that receipt. `status` prints the goal, section states, latest notes, last verification and files changed since the last status write.
- A `risk:` tag, whole-word risk terms, public-interface phrases, REQ IDs in a row or configured team coordination give that section standard evidence: a review verdict, a captured check, and a red checkpoint or a baseline bound to the original source, which `done` reruns as the same explicit check.
- Section risk estimates match whole words and their forms outside compatibility metadata, so `author` and `metadata` no longer count as `auth` and `data`, while `authorized`, `OAuth`, `passwords` and `migrate` do.
- Plan readers, team preparation, eval discovery and trial tools accept the host-neutral `plan.md` after `codex-plan.md` and `claude-plan.md`; a `plan.md` counts as a standard plan only with Forge metadata, so scratch files change nothing.

Default workflows are unchanged; the mode is described in [state-first](../skills/zagrosi-implement/references/state-first.md). No model trials were run for this release.

## 0.4.6 — Claude-only trial arms and a session-break protocol

- Prepare native acceptance matrices for Claude alone. `native_workflow_trials.py prepare --host claude` installs the package with Claude's own installer into disposable settings inside the matrix, checks skill discovery, and never runs Codex or writes to its cache. Two-host matrices also install Claude natively, from a separate marketplace copy, and remove their Codex cache namespace if preparation fails.
- Give Forge and plain Claude writers the same subagent and task tools (`Agent`, `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`); `TodoWrite` is off by default in Claude Code 2.1.304. Accept `xhigh` and `max` effort for Claude, and reject efforts a host does not support before launch, since Claude ignores unknown values.
- Count Claude tokens across the whole session tree. `totals` now come from the result's per-model usage, which includes subagents; `top_level_totals` keep the previous figures. Telemetry also records `subagent_stats`, the registered tools and any `missing_tools`.
- Add a draft [session-break evaluation protocol](../examples/evals/session-break-protocol.md) and a [state-first `lean` proposal](proposals/state-first-lean.md). Default workflow behavior is unchanged.

Runtime code is unchanged apart from regenerated manifests. No model trials were run for this release.

## 0.4.5 — Codex home in capability inventory

- `capability-inventory` without `--config` reads `$CODEX_HOME/config.toml` when `CODEX_HOME` is set, as `install-codex`, `update-check` and `self-update` already do. All four share one default-path helper.

## 0.4.4 — Claude Code host parity

- `doctor` and `update-check` compare Claude Code installs of Forge that apply to the current project with the source release and name the fix: `claude plugin marketplace update zagrosi`, then `claude plugin update zagrosi-forge@zagrosi`. They honor `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_PLUGIN_CACHE_DIR`. Doctor reports this without findings, so preflight scores do not change.
- Detect the running host from `CLAUDECODE=1` (Claude Code) or `CODEX_THREAD_ID` (Codex); both or neither means `other`. Generated `team start` commands, and team sessions started or recovered without `--host`, record it.
- `capability-inventory` also summarizes user-level Claude Code plugins and MCP servers, showing names, transports and yes/no flags only.
- The native smoke test fails when Claude Code loads Forge from anywhere but the staged package (local marketplaces load in place) or its disposable settings. It also fails when a `claude plugin details` row's count does not match its names; Claude Code 2.1.304 has no structured inventory. It ignores `CLAUDE_CODE_PLUGIN_*` overrides that could reach real plugin settings. CI pins Claude Code 2.1.304, and package drift tests cover the Claude install.
- Tests ignore the developer's Claude Code install and host markers.

## 0.4.3 — plan gates that explain themselves

- Verification findings name the exact gap: a case without `Expected:` or `Command:`, an unread label such as `Expected failure now:`, or an inspection rationale that does not cover the whole section.
- Sections of a multi-section compact plan are checked as deltas: a verified section contract supplies tests, implementation and acceptance, owned files show ownership, and goal, dependency, risk and rollback coverage comes from the shared plan and index.
- Unrecognized index dependencies suggest the full section names; word-budget findings explain how Forge counts words.

## 0.4.2 — same-host independent review

- Add the read-only `forge-reviewer` agent. Claude Code runs same-host independent review in a fresh context that reads the repository itself, without a second CLI sign-in; other hosts with subagents use the same instructions.
- Implement and Cleanup ask a fresh-context subagent to review behavior changes, the diff and affected callers where the host supports one. `provider-review` remains for other providers: Codex or Gemini from Claude Code, Claude or Gemini from Codex.
- Package `agents/` for both hosts, require a new release when it changes, include it in trial provenance, and check that Claude Code discovers the agent in the native smoke test.

## 0.4.1 — Claude Code host fixes

- Deliver runtime fixes to Claude Code installs: Claude Code keeps a cached plugin until its manifest version changes, so pull-request validation now fails when `skills/` or `scripts/` change without a new release version.
- Resolve the helper path in every skill, including the router and cleanup. Where the host supports it, the router loads phases as skills, so Claude Code substitutes the plugin root.
- Pre-approve Forge's read-only status helpers (`team status`, `status`, `next-section`) in Claude Code, including the Windows `python` and `py -3` launchers. Commands that write files, run checks, contact models or push still ask first.
- Keep captures within host command limits: `implement-verify` defaults to 540 seconds, skills give host commands a longer timeout or background execution, and SIGTERM now stops the owned check process group.
- Keep each provider's own failure message in `provider-review` reports, and report sign-in failures as authentication with the native login command.

## 0.4.0 — collaboration across engineers

- Share task awareness and atomic path reservations across clones and worktrees through an opt-in Git board.
- Keep ownership, checkout identity and recovery generations separate from display names; preserve interrupted publications for exact retries.
- Check configured mutable workflows before activation, verification and recording. Share the same task lifecycle across every skill, depth and host.
- Support explicit handoff, scope expansion, offline status and durable delivery notes without another service or runtime dependency.
- Prepare committed shared contracts into private workspaces so engineers can implement independent sections without sharing mutable progress or verification receipts. Coordinate canonical plan readers, edits and version changes through the same board.
- Diagnose ignored discovery before setup publishes a board; retain existing participants' recovery operations and leave ignore rules unchanged.
- Preserve solo behavior and local verification evidence. Reservations coordinate participating clients; they do not lock external editors or prove semantic compatibility. Detached frozen execution uses external team checks.

See [team onboarding and recovery](collaboration.md). No native model sign-in is
required for the shared protocol; offline tests are separate from live host acceptance.

## 0.3.5 — stronger quality evidence and smoother delivery

- Continue authorized delivery across phases without repeating generic preference interviews.
- Explain provider failures with specific recovery; optionally check native CLI arguments through bounded help probes, without model or authentication calls. Inconclusive help remains unknown.
- Check public TypeScript types with a pinned development-only compiler before running the independent runtime oracle.
- Bind new native trial evidence to the complete declared package and native loading metadata; keep historical reports readable without relabeling their coverage.
- Exercise whole workflow sequences at lean, standard and deep; add a tenant-isolated retry bug-fix trial with an independent oracle.
- Separate cached and uncached input usage per accepted result. Split Windows compatibility groups while retaining their combined coverage.
- Give fresh coding trials a clean Git baseline for reliable change inspection, preserving parent repositories and historical trials.
- Keep original source promises in compact handoffs and check refactoring assumptions against them; characterize uncertain behavior before extending duplication.
- Protect Python caller contracts with independent error, iteration, signature and export checks; bind blind review checks to their source results and disclose required cleanup equally to every arm.

- Capture declared preservation checks against activated original source, then replay identical check bytes and arguments against the candidate before completion. Keep legacy plans and detached contracts compatible.
- Require an explicit preservation decision in new mutable plans; share one compact authoring guide and retain failed attempts, actionable recovery and honest authorship limits.
- Separate common code acceptance from Forge workflow completion. Add independently authored configuration, publication, streaming and event tasks with positive controls and deliberate regressions; retain all comparative outcomes.
- Reject malformed verification sources with structured errors and preserve saved receipts. Document editable evidence and explicit preservation-test selection.
- Ground reviews in concrete inputs, calls and failure paths; verify alleged defects and challenge uncertain behavior with isolated counterexamples. Share this guidance across both hosts and every depth.
- Strengthen future publication trials against lost existing permission bits, valid long-name failures and temporary-file leaks after real staging failures; retain correct alternative implementations and frozen historical results.

The plugin runtime remains Python standard-library only. Live Claude/Gemini and cross-host acceptance still require native sign-in; offline checks do not establish model access or general code-quality superiority.

## 0.3.4 — reliable continuation and clearer reviews

- Bind completed work to its target repository and preserve malformed saved evidence without overwriting it.
- Support directory ownership when recording progress and completion, including descendant changes.
- Continue directly to final checks when captured integration evidence is current.
- Keep recovery actions, blockers, provider availability and model identity visible in readable reports.
- Make multi-provider review easier to find and start in either host.
- Reuse completed analyses in portable validation and release isolated test runtimes after use.

## 0.3.3 — provider review validation

- Accept completed Codex review text only and reject unfinished review turns.
- Reject malformed provider model and tool metadata before accepting a review.

## 0.3.2 — project session validation

- Reject unreadable or non-object saved project sessions before creating requirements files.
- Report invalid session state consistently during setup and project checks.

## 0.3.1 — nested repository verification

- Capture verification in repositories containing submodules or embedded Git repositories.
- Bind nested source changes, staged submodule revisions and repository boundaries while preserving ignored-file and symlink handling.

## 0.3.0 — shared quality workflows

- One entry skill and a dedicated cleanup skill, shared by Codex and Claude Code.
- Full selected-depth planning admission, explicit verification outcomes, and fresh integration evidence; failed checks cannot establish completion.
- Ownership-aware parallel work, portable bundled checks, and actionable context-budget recovery.
- Optional selected-provider reviews through existing CLI authentication.
- Native skill discovery and upgrade checks, plus broader behavioral evaluation fixtures.

`pyproject.toml` is the version source. Run `python3 tools/sync_release.py` after
changing it; CI rejects mismatched Codex or Claude manifests. Changes intended
for distribution require a new version: a marketplace refresh alone may leave
an installed same-version cache unchanged.

`python3 tools/native_plugin_smoke.py --require --upgrade` exercises both
installed CLIs with disposable settings and no model calls. It checks native
discovery, installed files, references, doctor, a version change, and whether a
same-version content change leaves stale bytes. Local-source upgrade checks do
not establish hosted marketplace propagation or model-led skill quality.

## 0.2.0 — initial shared host package

Compact project, plan and implementation workflows; Claude Code native metadata;
portable package, recovery and installer checks. Earlier Codex builds used a
timestamp suffix. Historical trial reports remain unchanged; they do not prove
general speed or code-quality gains.
