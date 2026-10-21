# Releases

## 0.4.7 — Claude session-break run

- Claude writers no longer inherit the launching Claude Code session's environment. A trial started from inside Claude Code received that session's id, messaging socket, entrypoint and plugin data, plus the effort and model aliases from its settings, such as `ANTHROPIC_DEFAULT_OPUS_MODEL`. Only credentials and `CLAUDE_CONFIG_DIR` pass through, and telemetry lists the removed names.
- Claude auto-memory is off for every writer (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`). It is on by default and keyed by the workspace path, so a second session could read what the first one saved.
- The Claude Forge arm may read its plugin tree (`--add-dir`). Under `--restricted`, Forge's skills could not open their own references with `Read` and fell back to shell commands.
- Session-break reports count each Claude session's observed input tokens from its log, subagents included. A session that times out reports no totals, so this is the only usage it leaves. Re-orientation tokens are now main-loop input only, because the log repeats each response's first output count.

Runtime code is unchanged apart from regenerated manifests.

## 0.4.4 — session-break trials

- Run the [session-break protocol](../examples/evals/session-break-protocol.md) end to end with `tools/session_break_trials.py`: any arm through a forced break after section 2, a checkpoint with per-test, oracle and clause-probe results, and a fresh second session that cannot read the first session's files. Reports add re-orientation time, regressions against the checkpoint and blind review through the existing comparison packets.
- `--probe` removes the withheld decision clauses from each brief before the baseline is recorded and gives them in session 1 only. Tests confirm that violating each clause fails the unchanged oracle while a good control passes.
- Claude writers no longer save session transcripts (`--no-session-persistence`), so a later session cannot find an earlier one on disk.
- First [Codex results](../examples/evals/session-break-results-2026-10-20.md): both stateful arms kept a pre-break decision that plain agents lost in 4 of 4 attempts. Notes matched Forge `lean` (6/8 accepted each, plain 4/8) at a sixth of its tokens, which supports H1 but not H2.

Runtime code is unchanged apart from regenerated manifests.

## 0.4.3 — Claude-only trial arms and a session-break protocol

- Prepare native acceptance matrices for Claude alone. `native_workflow_trials.py prepare --host claude` installs the package with Claude's own installer into disposable settings inside the matrix, checks skill discovery, and never runs Codex or writes to its cache. Two-host matrices also install Claude natively, from a separate marketplace copy, and remove their Codex cache namespace if preparation fails.
- Give Forge and plain Claude writers the same subagent and task tools (`Agent`, `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`); `TodoWrite` is off by default in Claude Code 2.1.304. Accept `xhigh` and `max` effort for Claude, and reject efforts a host does not support before launch, since Claude ignores unknown values.
- Count Claude tokens across the whole session tree. `totals` now come from the result's per-model usage, which includes subagents; `top_level_totals` keep the previous figures. Telemetry also records `subagent_stats`, the registered tools and any `missing_tools`.
- Add a draft [session-break evaluation protocol](../examples/evals/session-break-protocol.md) and a [state-first `lean` proposal](proposals/state-first-lean.md). Default workflow behavior is unchanged.

Runtime code is unchanged apart from regenerated manifests. No model trials were run for this release.

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
