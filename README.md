# Zagrosi Forge

<p>
<picture>
  <source media="(max-width: 600px)" srcset="assets/readme-hero-mobile.svg" />
  <img src="assets/readme-hero.svg" alt="Forge turns briefs into reviewed plans and tested code, in Codex and Claude Code." width="100%" />
</picture>
</p>

Forge turns requests into compact plans, clear code, and verified changes.
One entry point handles features, fixes, and cleanup in Codex and Claude Code.
Lean mode is default: focused context, targeted checks, one final integration result.

[Install](#install) · [Use](#use) · [Multiple models](#multiple-models) · [Workflows](#workflows) · [Engineering](#engineering) · [Releases](docs/releases.md) · [Contribute](#contribute)

## Install

Requires Python 3.11+ and host plugin support. On Windows, replace `python3`
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

Claude Code can load this checkout for one session:

```bash
claude --plugin-dir "/absolute/path/to/zagrosi-forge"
```

For Codex, run these from the checkout:

```bash
python3 scripts/zagrosi_skills.py install --pretty
# Preview without writing:
python3 scripts/zagrosi_skills.py install --dry-run --pretty
# Refresh from the updated checkout:
git pull --ff-only
python3 scripts/zagrosi_skills.py update-check --pretty
python3 scripts/zagrosi_skills.py self-update --pretty
```

The installer preserves unrelated settings and excludes development artifacts.

</details>

## Use

Open your repository and describe the task; request `standard` or `deep`
when needed. Try the [runnable first task](examples/first-task/README.md).

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

Both hosts share skills, runtime, and standards. Forge adds no hooks, MCP servers,
or background processes. Use `zagrosi-project`, `zagrosi-plan`, and
`zagrosi-implement` for direct phase control. Planning-only requests stop at the plan.

## Multiple models

Request independent Codex, Claude, or Gemini reviews. Keep one primary writer;
reviewers receive the same bounded packet. Try either host:

**Claude Code**

```text
/zagrosi-forge:zagrosi-forge Implement this feature with independent Codex and Claude reviews. Keep one writer; report missing access.
```

**Codex**

```text
Use $zagrosi-forge:zagrosi-forge to implement this feature with independent Codex and Claude reviews. Keep one writer; report missing access.
```

Forge reuses native CLI logins. Choose exact model IDs or use native defaults;
missing access and unreported identity stay visible. No silent fallback.
See [provider setup and two-reviewer example](skills/zagrosi-forge/references/providers.md).

```bash
python3 scripts/zagrosi_skills.py provider-status --check-auth --pretty
```

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
| Multi-section plan | One shared plan and ordered sections linking shared contracts |
| Implement | Code, tests, and machine-readable section records |
| Cleanup | Simpler responsibilities and preserved behavior, with regression evidence |

| Depth | Use |
|---|---|
| `lean` — default | Minimum sufficient artifacts and focused checks |
| `standard` | Wider research, traceability, or coordination |
| `deep` | High-risk or architecture-heavy work |

Depth changes investigation and review. Every mode retains requirements,
ownership, acceptance, and verification without duplicate ledgers.

Skills handle setup and final checks; complete draft contracts before coding. See the
[compact-plan format](skills/zagrosi-plan/references/plan-format.md),
[depth standards](skills/zagrosi-plan/references/depth-standards.md), and
[example briefs](examples/gallery/README.md).

### Resume and compatibility

Continue from the planning directory in either host. Checkpoints retain progress;
ownership conflicts serialize work. Changed code/contracts require fresh verification.
Legacy evidence cannot establish new verified completion.

Run helpers **from the Forge checkout**, using absolute paths for external plans.

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
draws on [Ponytail](https://github.com/DietrichGebert/ponytail): trace callers, reuse
code, prefer direct flow. Repair encountered duplication and mixed responsibilities;
preserve public contracts. Run targeted regressions, then one integration suite.
Explicit cleanup can cover a repository; final PR review covers the diff and callers.

`context-brief` and `implementation-packet` preserve complete sections and linked
contracts within 2,000 words (`--max-words`). Broken links fail explicitly;
oversized sections return an adjusted retry. Readable output retains blockers
and recovery commands; failed flights link complete reports (`--full-output`).

Runtime loading verifies SHA-256 source bindings; writes are locked and atomic.

[Helper benchmarks](examples/evals/performance.json) measure fixed fixtures.
[Coding trials](examples/evals/coding/README.md) separate host/model, accepted
completion, cleanup quality, time, and usage. Historical trials remain unchanged;
adapter support alone does not establish faster or better model output.

## Contribute

Shared workflows: `skills/`; runtime: `scripts/forge/`; checks: `tests/` and
`examples/`; host metadata: `.codex-plugin/` and `.claude-plugin/`.

After runtime/test edits, run `python3 tools/update_runtime_manifest.py`.
After adding/removing package members, stage intended files and run
`python3 tools/update_package_manifest.py`.
CI covers Linux/macOS/Windows and native host packaging. Optional native validators
require their CLIs.

```bash
python3 tools/update_runtime_manifest.py --check
python3 tools/update_package_manifest.py --check
uv run --with pytest python -m pytest
python3 scripts/zagrosi_skills.py doctor --plugin-root . --strict --pretty
python3 scripts/zagrosi_skills.py release-check --plugin-root . --pretty
plugin-scanner verify .
claude plugin validate . --strict
```

[MIT License](LICENSE) · [Attribution](NOTICE.md)
