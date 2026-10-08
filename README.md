# Zagrosi Forge

<p>
<picture>
  <source media="(max-width: 600px)" srcset="assets/readme-hero-mobile.svg" />
  <img src="assets/readme-hero.svg" alt="Forge turns briefs into reviewed plans and tested code, in Codex and Claude Code." width="100%" />
</picture>
</p>

Forge turns requests into compact plans, clear code, and verified changes.
For features, fixes and cleanup in Codex and Claude Code.
Lean by default: focused context, targeted checks, one integration result.

[Install](#install) · [Use](#use) · [Multiple models](#multiple-models) · [Teams](#teams) · [Workflows](#workflows) · [Engineering](#engineering) · [Releases](docs/releases.md) · [Contribute](#contribute)

## Install

Requires Python 3.11+ and plugins. On Windows, replace `python3`
with `python` or `py -3`. Restart after installing or updating.

### Claude Code

```bash
claude plugin marketplace add zagrosi-code/zagrosi-forge
claude plugin install zagrosi-forge@zagrosi
```

Update with `claude plugin marketplace update zagrosi`, then
`claude plugin update zagrosi-forge@zagrosi`.

### Codex

```bash
codex plugin marketplace add zagrosi-code/zagrosi-forge
codex plugin add zagrosi-forge@zagrosi
```

<details>
<summary>Install or develop from a local checkout</summary>

```bash
git clone https://github.com/zagrosi-code/zagrosi-forge.git
cd zagrosi-forge
```

Load this checkout in Claude Code for one session:

```bash
claude --plugin-dir "/absolute/path/to/zagrosi-forge"
```

From the checkout in Codex:

```bash
python3 scripts/zagrosi_skills.py install --pretty
# Preview without writing:
python3 scripts/zagrosi_skills.py install --dry-run --pretty
# Refresh from the updated checkout:
git pull --ff-only
python3 scripts/zagrosi_skills.py update-check --pretty
python3 scripts/zagrosi_skills.py self-update --pretty
```

</details>

## Use

Describe your task; request `standard` or `deep`
when needed. Try the [first task](examples/first-task/README.md).

**Claude Code**

```text
/zagrosi-forge:zagrosi-forge Add this feature and finish verification.
/zagrosi-forge:zagrosi-cleanup Simplify this subsystem while preserving behavior.
```

**Codex**

```text
Use $zagrosi-forge:zagrosi-forge to add this feature and finish verification.
Use $zagrosi-forge:zagrosi-cleanup to simplify this subsystem while preserving behavior.
```

Both hosts share skills, runtime and standards. Use `zagrosi-project`, `zagrosi-plan`, and `zagrosi-implement`
for individual phases. Authorized work continues through delivery; planning-only requests stop at the plan.

## Multiple models

Ask Forge for independent Codex, Claude or Gemini reviews. Reviewers receive the
same bounded packet:

```text
Implement this feature with independent Codex and Claude reviews. Keep one writer; report missing access.
```

Forge reuses native CLI logins. Choose model IDs or native defaults;
missing access and unreported identity stay visible. No silent fallback.
Add `--check-auth` for login status. See [provider setup](skills/zagrosi-forge/references/providers.md).

```bash
python3 scripts/zagrosi_skills.py provider-status --check-cli --pretty
```

## Teams

See teammates' tasks and reserve work across clones, worktrees and both hosts.
Ask Forge to enable collaboration, commit `.forge/team.json`, and have teammates
join. Share reviewed plans through Git; prepare private workspaces for independent
section work. No extra service.

Every depth and cleanup shares reservations. Stale tasks retain ownership; offline
status grants no clearance. Notes remain in Git history.
See [setup, handoffs and recovery](docs/collaboration.md).

## Workflows

<p>
<picture>
  <source media="(max-width: 600px)" srcset="assets/readme-workflow-mobile.svg" />
  <img src="assets/readme-workflow.svg" alt="Project splits a brief into specs; Plan defines a compact contract; Implement tests, builds, reviews, and records each section." width="100%" />
</picture>
</p>

| Workflow | Output |
|---|---|
| Project | `project-manifest.md` and child `spec.md` files |
| Single-section plan | `sections/index.md` and one canonical section with tests, evidence, decisions, risks, and review |
| Multi-section plan | Shared plan and ordered sections |
| Implement | Code, tests, and machine-readable section records |
| Cleanup | Simpler responsibilities and preserved behavior, with regression evidence |

| Depth | Use |
|---|---|
| `lean` — default | Minimum sufficient artifacts and focused checks |
| `standard` | Wider research, traceability, or coordination |
| `deep` | High-risk or architecture-heavy work |

Depth changes investigation and review. Every mode retains requirements,
ownership, acceptance, and verification without duplicate ledgers.

Complete contracts before coding. See
[compact-plan format](skills/zagrosi-plan/references/plan-format.md),
[depth standards](skills/zagrosi-plan/references/depth-standards.md), and
[example briefs](examples/gallery/README.md).

### Resume and compatibility

Continue from the planning directory in either host. Checkpoints retain progress;
ownership conflicts serialize work. Changed code/contracts require fresh verification.
Legacy evidence cannot establish new verified completion.

Run helpers **from the Forge checkout**; use absolute plan paths.

```bash
python3 scripts/zagrosi_skills.py status --path "/absolute/path/to/planning/01-auth" --pretty
python3 scripts/zagrosi_skills.py next-section --planning-dir "/absolute/path/to/planning/01-auth" --pretty
python3 scripts/zagrosi_skills.py commands --phase implement --verbose --pretty
```

`fast` aliases `lean`; existing `zagrosi-*`, `deep-*`, `DEEP_META`, and migrated
`claude-*` artifacts remain accepted. `codex_review` aliases `agent_review`.

[Detached execution](skills/zagrosi-implement/references/detached-frozen.md) retains
its pinned evidence protocol and physical contracts; privileged handoff requires
Darwin/arm64 and APFS.

## Engineering

The [engineering standard](skills/zagrosi-implement/references/engineering.md)
follows [Ponytail](https://github.com/DietrichGebert/ponytail): trace callers, reuse
code, prefer direct flow. Simplify duplication and responsibilities with
regression coverage. [Preservation checks](skills/zagrosi-implement/references/compatibility.md)
run before edits, then replay unchanged checks and commands; required pairs gate
completion. Capture cannot prove coverage or authorship. Run feature checks and
integration. Cleanup can cover repositories; PR review covers the diff and callers.

`context-brief` and `implementation-packet` keep complete sections, mapped source
requirements and linked contracts. Budget: 2,000 words (`--max-words`);
broken links fail and oversized sections return an adjusted retry. Failures retain
blockers, recovery commands and complete reports (`--full-output`).

Runtime loading verifies SHA-256 source bindings; writes are locked and atomic.

[Helper benchmarks](examples/evals/performance.json) measure fixed fixtures.
[Coding trials](examples/evals/coding/README.md) separate code quality, workflow
completion, time, and usage. [Latest observations](examples/evals/coding/behavior-contract-results-2026-10-02.md) retain failures;
adapter support alone does not establish faster or better model output.

## Contribute

Shared workflows: `skills/`; runtime: `scripts/forge/`; checks: `tests/` and
`examples/`; host metadata: `.codex-plugin/` and `.claude-plugin/`.

After runtime/test edits, run `python3 tools/update_runtime_manifest.py`.
After adding/removing package members, stage intended files and run
`python3 tools/update_package_manifest.py`.
CI covers Linux/macOS/Windows and native host packaging. Native validators require their CLIs.

```bash
python3 tools/update_runtime_manifest.py --check
python3 tools/update_package_manifest.py --check
npm ci --prefix tools
uv run --with pytest python -m pytest
python3 scripts/zagrosi_skills.py doctor --plugin-root . --strict --pretty
python3 scripts/zagrosi_skills.py release-check --plugin-root . --pretty
plugin-scanner verify .
claude plugin validate . --strict
```

[MIT License](LICENSE) · [Attribution](NOTICE.md)
