# Zagrosi Forge

<p>
<picture>
  <source media="(max-width: 600px)" srcset="assets/readme-hero-mobile.svg" />
  <img src="assets/readme-hero.svg" alt="Forge turns briefs into reviewed plans and tested code, in Codex and Claude Code." width="100%" />
</picture>
</p>

Forge turns requests into focused specs, reviewed plans, and tested changes.
Lean mode is default: fewer files, shorter prompts, one setup, one final gate.

[Install](#install) · [Use](#use) · [Workflows](#workflows) · [Engineering](#engineering) · [Contribute](#contribute)

## Install

Requires Python 3.11+ and plugin support in your host. Commands below use
`python3`; on Windows, use your Python 3.11+ `python` or `py -3` interpreter.
Restart your host after installing or updating.

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

The Codex installer copies declared package files, preserves unrelated settings,
excludes development artifacts, and keeps a recoverable installation.

</details>

## Use

Open your target repository in either host. Start at the phase you need;
ask for `standard` or `deep` when needed.

**Claude Code**

```text
/zagrosi-forge:zagrosi-project @planning/requirements.md
/zagrosi-forge:zagrosi-plan @planning/01-auth/spec.md
/zagrosi-forge:zagrosi-implement @planning/01-auth/sections/
```

**Codex**

```text
Use $zagrosi-forge:zagrosi-project on @planning/requirements.md
Use $zagrosi-forge:zagrosi-plan on @planning/01-auth/spec.md
Use $zagrosi-forge:zagrosi-implement on @planning/01-auth/sections/
```

Both hosts share the same skills, runtime, and engineering standards.
Forge adds no hooks, MCP servers, or background processes.

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

| Depth | Use |
|---|---|
| `lean` — default | Minimum sufficient artifacts and focused checks |
| `standard` | Wider research, traceability, or coordination |
| `deep` | High-risk or architecture-heavy work |

Depth changes investigation and review rigor. Every mode uses compact contracts
with requirement IDs, ownership, acceptance, and verification.
No minimum prose quotas or duplicate research/review ledgers.

The skills handle setup and final postflight. Complete the generated draft before
implementation; a scaffold is never an admitted plan. See the
[compact-plan format](skills/zagrosi-plan/references/plan-format.md),
[depth standards](skills/zagrosi-plan/references/depth-standards.md), and
[example briefs](examples/gallery/README.md).

### Resume and compatibility

Ask the same skill to continue from the planning directory. Saved records identify
progress; contract edits reopen completion and code drift requires integration checks.
Legacy records remain readable, labeled unbound.

Helper commands below run **from the Forge source checkout**. Use absolute paths
for plans in another repository. `status` shows the next action;
`commands --pretty` lists key commands; `--help` exposes the full CLI.

```bash
python3 scripts/zagrosi_skills.py status --path "/absolute/path/to/planning/01-auth" --pretty
python3 scripts/zagrosi_skills.py commands --pretty
python3 scripts/zagrosi_skills.py --help
```

`fast` remains a compatibility alias for `lean`. Existing `zagrosi-*`, `deep-*`,
`DEEP_META`, and migrated `claude-*` artifacts remain accepted. `codex_review`
aliases `agent_review`: review uses the active agent without another model CLI.

[Detached authoring](skills/zagrosi-plan/references/detached-plan-format.md)
uses `plan-setup --for-detached` to skip compact scaffolding.
[Detached execution](skills/zagrosi-implement/references/detached-frozen.md)
requires `--implementation-root`, admitted physical contracts and source checks;
privileged handoff additionally requires Darwin/arm64 and APFS.

## Engineering

The shared [engineering standard](skills/zagrosi-implement/references/engineering.md)
draws on [Ponytail](https://github.com/DietrichGebert/ponytail): trace callers, reuse
code, prefer clear names and direct flow. Repair encountered duplication and mixed
responsibilities; update ownership, characterize behavior, run targeted regression
checks, then the full suite at integration.

Context preserves complete sections, linked decisions/risks, and explicit omissions.
`context-brief` and `implementation-packet` default to 2,000 words (`--max-words`).
Broken links and oversized contracts fail explicitly. Failed flights link complete
reports; `--full-output` includes the nested payload.

The runtime loads modules on demand. SHA-256 binds runtime/test sources; verification
compiles those exact bytes without bytecode caches. Writes are locked and atomic.

[Helper benchmarks](examples/evals/performance.json) measure latency and context on
fixed fixtures. [Coding trials](examples/evals/coding/README.md) report task outcomes,
failures and usage. Neither establishes general speed or code-quality gains;
existing model trials use Codex, not Claude Code.

## Contribute

`skills/` holds shared workflows; `scripts/forge/` holds runtime modules.
`tests/` covers contracts, recovery and installation; `examples/` includes valid,
invalid and compatibility fixtures. Host metadata lives in `.codex-plugin/` and
`.claude-plugin/`; Codex packaging uses `.codex-plugin/package-files.json`.

After runtime/test edits, run `python3 tools/update_runtime_manifest.py`.
After adding/removing package members, stage intended files and run
`python3 tools/update_package_manifest.py`.
CI runs the full Linux suite and focused Linux/macOS/Windows checks.
Run these from the checkout; the final two validators require their optional CLIs.

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
