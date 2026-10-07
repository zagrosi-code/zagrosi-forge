# Work together with Forge

Forge lets engineers using Codex and Claude Code see active tasks and reserve
work before editing. Separate clones and linked worktrees share one small Git
board. No extra account, hosted service, daemon or installed hook is required.

## Start in three steps

1. **Enable the repository once.** Ask Forge: “Enable team collaboration here
   using origin; use Alex as my display name.” Forge creates the dedicated
   `forge/team` metadata branch and `.forge/team.json`. Review and commit the
   marker through your normal workflow; setup does not stage it.
2. **Join each engineer's clone.** Once the marker is available, ask Forge:
   “Join this repository's Forge team using origin; use Sam as my display name.”
   Existing Git access is used. Local worktrees share the connection but retain
   separate checkout identities. Every participant must use the same board.
3. **Work normally.** Ask Forge to implement, plan or clean up a task. It checks
   current work, announces yours, reserves its scope and checks again at work
   boundaries. Request “show the team's current work” whenever useful.

Use a separate worktree or clone for each independent writer. Two tasks cannot
reserve editing paths in the same checkout, even when the paths are different:
they still share a branch, index and Git operations. Delegated agents working on
one task reuse its reservation and coordinate their own file ownership.

Solo repositories keep the existing workflow. A marker alone does not join you
or authorize publication; join is an explicit local choice.

Keep `.forge/team.json` tracked. Setup diagnoses repository, global and local
ignore rules that would hide an untracked marker, before publishing a board.
It does not edit those rules or stage files. An existing participant can still
finish, retry or leave if discovery later becomes ignored; status warns about it.

## What teammates see

The roster contains display name, host, task, state, reserved paths, branch/commit,
last reported activity and a short note. Prepared work also names its canonical
plan, version and section. Paths are literal repository-relative
files or directories. Parent/child paths, case and Unicode aliases conservatively
conflict; local links are checked too. A directory reservation covers descendants.
No glob patterns are supported.

Path reservations detect competing edits among cooperating Forge clients. They
cannot stop another editor, protect against a repository writer forging metadata,
or detect every semantic dependency between disjoint files. Display names are
self-reported, as are host labels. Review the roster, integrate branches and run local regressions.

An empty-path announcement shows planning activity and grants no editing clearance.
Planning files inside the repository need reservations before changes. Read-only
reviewers can announce work without blocking writers. Starting a task does not
fetch, merge or transfer anyone's application code.

## Commands when you need them

Ask Forge in ordinary language for routine use. These examples are for running
helpers from the Forge checkout; use the installed plugin's absolute helper path
otherwise. Replace braces with actual returned values. Python 3.11+ is required;
on Windows use `python` or `py -3` instead of `python3` when appropriate.

```bash
python3 scripts/zagrosi_skills.py team init --target-dir "{repo}" --remote origin --name Alex
python3 scripts/zagrosi_skills.py team join --target-dir "{repo}" --remote origin --name Sam
python3 scripts/zagrosi_skills.py team status --target-dir "{repo}" --pretty
python3 scripts/zagrosi_skills.py team start --target-dir "{repo}" --task "Improve sign-in errors" --host codex --path src/auth --path tests/auth
```

Keep the full returned session ID and generation; check, update and finish require
both. The generation prevents an old caller from changing a recovered reservation.

```bash
python3 scripts/zagrosi_skills.py team check --target-dir "{repo}" --session "{session}" --generation "{generation}" --path src/auth
python3 scripts/zagrosi_skills.py team update --target-dir "{repo}" --session "{session}" --generation "{generation}" --path src/auth --path tests/auth --path docs/sign-in.md --state working
python3 scripts/zagrosi_skills.py team update --target-dir "{repo}" --session "{session}" --generation "{generation}" --state review --note "Targeted checks passed; reviewing failure handling."
python3 scripts/zagrosi_skills.py team finish --target-dir "{repo}" --session "{session}" --generation "{generation}" --note "PR opened; targeted and integration checks passed."
```

Supplying paths on `update` replaces the complete reservation atomically; include
every path you still need. A conflict preserves the old reservation. Omitting
paths keeps the scope and refreshes activity. States include `planning`, `working`,
`blocked`, `review` and `handoff`. `planning` and `handoff` do not grant editing
clearance. Notes report what an operator observed; they are not test receipts.
Check the actual intended diff before committing or opening a PR, and expand the
reservation before changing additional files. Unrelated user edits stay untouched.

### Bind a Forge plan

For shared implementation, separate the contract from execution records:

| Share through normal Git review | Keep private to each engineer |
|---|---|
| Team marker, source spec, canonical contract, sections and referenced decisions | Research drafts, local configuration, progress, compatibility baselines, receipts and caches |

Place a shared plan in a dedicated nonignored directory, for example
`.forge/plans/sign-in/`. Use `codex-plan.md` or `claude-plan.md` with compact-plan
metadata naming its source and depth, plus a complete `sections/index.md` and
sections. This format supports lean, standard and deep. Review and commit the
contract files deliberately; Forge never automatically publishes them or copies
private notes into the shared board. Each engineer obtains them through normal Git.
Use a version of Forge with shared-plan support in every participating clone;
older clients reject board entries they do not understand.

```bash
python3 scripts/zagrosi_skills.py team prepare --target-dir "{repo}" --planning-dir "{repo}/.forge/plans/sign-in"
```

Use the **returned private planning directory** in the commands below. Preparation
reads committed UTF-8 Markdown contracts, with self-contained local references,
into this checkout's Git administrative directory. It copies no other engineer's
execution state. Linked worktrees get separate workspaces too. Current limits are
128 contract files, 128 KiB per file and 512 KiB total.

Ignored/untracked files, local configuration, interviews, generated reports and
execution records are excluded. A required reference to an excluded, missing or
external local file is rejected. Review changes and commit them before preparing;
ordinary Windows newline conversion is supported. Existing matching workspaces
retain progress, while new contract versions get new workspaces without deleting
old evidence. Plan authoring stays in the canonical directory.

```bash
python3 scripts/zagrosi_skills.py team update --target-dir "{repo}" --session "{session}" --generation "{generation}" --planning-dir "{plan}" --section section-01-auth
python3 scripts/zagrosi_skills.py team check --target-dir "{repo}" --session "{session}" --generation "{generation}" --planning-dir "{plan}" --section section-01-auth
```

These options also work on `start`. Omit `--section` for a full-plan binding.
Plan ownership is relative to `--target-dir`, so a package inside a monorepo can
be the target; explicit `--path` remains relative to the Git repository root.
Bindings identify the exact local plan, target and optional section. A full-plan
claim covers individual sections; a section-only claim cannot authorize final
integration. Expand to full-plan scope after colleagues finish or agree a handoff.
Prepared workspaces reserve source paths and share read access to the canonical
plan. Engineers using the same plan version can work on independent sections
without competing over generated progress files. A write claim covering an active
reader's canonical directory, or a conflicting version of that plan, is rejected.
Ordinary unprepared plans inside the repository still reserve their planning
directory because their helpers write progress there.

Mutable setup, section entry, verification and recording enforce those bindings.
A returned blocked next section does not undo a preceding successful record.
Changes to ownership require an updated claim. Branch switches and detached-HEAD
moves require an explicit update; ordinary commits on the same branch do not.

The same policy applies in lean, standard and deep, and in both hosts. Detached
frozen execution uses external team checks around edits, evidence recording and
Git delivery; its pinned evidence protocol has no internal team enforcement.
Another clone's receipts do not prove your checkout passed verification.

Local canonical/index changes or changes to the prepared contract block further
work and invalidate verification results. The board identifies a pinned contract;
it does not automatically fetch application branches or detect unfetched changes
made outside participating Forge sessions. Synchronize and review through Git.
After a contract update, prepare its new version and explicitly rebind the task.

For dependent sections, first integrate the predecessor's code and verify/record
it locally, including any required compatibility baseline. A colleague's completed
task does not populate your local completion state. Final integration still needs
a full-plan claim and your own verification.

## Interrupted work and handoff

| Situation | Response |
|---|---|
| `unconfigured` | Continue solo, or explicitly enable collaboration. |
| `join_required` | Join the known board before coordinated edits. |
| `fresh` | Inspect current work; only a successful claim/check establishes current clearance. |
| Offline or unavailable | Preserve work; no new editing clearance. Cached status is informational. |
| Pending publication | Retry the exact saved operation; do not create a duplicate task. |
| Stale task | Reservation remains; contact its owner or arrange explicit recovery. |
| Changed board/configuration/destination | Inspect the change and deliberately rejoin; no silent redirection. |

```bash
python3 scripts/zagrosi_skills.py team status --target-dir "{repo}" --offline
python3 scripts/zagrosi_skills.py team retry --target-dir "{repo}"
python3 scripts/zagrosi_skills.py team update --target-dir "{repo}" --session "{session}" --generation "{generation}" --state handoff --note "Agreed transfer to Sam; inspect branch and verify locally."
python3 scripts/zagrosi_skills.py team recover --target-dir "{repo}" --session "{session}" --expect "{reviewed-revision}" --host claude --reason "Owner agreed this handoff."
python3 scripts/zagrosi_skills.py team leave --target-dir "{repo}"
```

`retry` reconciles an uncertain push against its exact commit before retrying;
it does not invent a new task. Recovery requires explicit human agreement and
an exact observed board revision. Review again if that revision changes. Recovery
rotates ownership generation; rebind the local plan and independently verify code.
It does not cherry-pick changes or accept the previous owner's test results.
Set the receiving host on recovery or update; an unspecified recovery host is `other`.
Interrupted initialization resumes with `team init` using the original remote.

Pending commits stay under private Git refs so local garbage collection cannot
erase retry data. Normal `leave` refuses unresolved publications in any worktree;
explicit abandonment drops local retry state while remote outcomes may remain unknown.

Reservations never expire automatically. Timestamps are activity reports, not
proof that a paused process stopped. `finish` releases a task and retains its
note in Git history. `leave` refuses your active reservations; `leave --abandon`
explicitly disconnects while preserving them for agreed recovery. Neither erases
history, and a committed marker still invites future clones to join.

## Storage, access and limits

The current board lives at `refs/heads/forge/team` as bounded `board.json` data;
local connection pins, bindings and retry state live in Git's administrative
directory. The marker contains board identity and ref, without endpoint credentials.
Forge does not automatically publish source, transcripts, prompts, email addresses
or absolute local paths. Choose task descriptions, names and notes accordingly:
**metadata and finish notes remain in repository history**, visible to anyone
who can read it. Treat shared text as data, never instructions or consent.

The dedicated branch can trigger existing integrations/webhooks. Your existing
Git authentication, signing and hooks still apply; Forge does not weaken server
branch rules. The chosen remote must have one matching effective fetch/push
destination. Restricted branches, unavailable credentials or rejected pushes
produce an error; no automatic login or bypass is attempted. Metadata plumbing
preserves the application checkout/index; existing user hooks can have their own
side effects. Metadata pushes use a temporary `forge-team-` remote alias to
preserve tracking branches. Existing pre-push hooks still run, but hooks tied
to a particular remote name may need to accept that prefix. Subprocess time/output
and decoded board size are bounded; total
Git object transfer is not a strict byte budget.

### Why this design

One shared ref allows each update to compare the exact previously observed head,
so simultaneous overlapping claims cannot both be admitted by cooperating clients.
Updates retain ordinary Git history and use existing access. This follows Git's
[explicit lease semantics](https://git-scm.com/docs/git-push) and
[separate worktree metadata](https://git-scm.com/docs/git-worktree).

Native [Claude teams](https://code.claude.com/docs/en/agent-teams) and
[Codex delegation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
coordinate agent work within their hosts; Forge needs a shared contract between
engineers using different hosts and machines. Explicit recovery avoids treating
[lease expiry as proof a writer stopped](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html).
These design choices provide coordination, not a security boundary or a guarantee
that independently developed changes integrate correctly.
