<div align="center">
  <img src="assets/icon.svg" alt="Zagrosi Forge icon" width="96" height="96" />
  <h1>Zagrosi Forge</h1>
  <p><strong>Lean project decomposition, planning, and test-first implementation for Codex and Claude Code.</strong></p>
  <p>
    <a href="https://github.com/zagrosi-code/zagrosi-forge"><img alt="Codex plugin" src="https://img.shields.io/badge/Codex-plugin-0F766E?style=flat-square" /></a>
    <a href="https://code.claude.com/docs/en/plugins"><img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude_Code-plugin-D97757?style=flat-square" /></a>
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-334155?style=flat-square" />
    <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-111827?style=flat-square" />
  </p>
  <img src="assets/readme-hero.svg" alt="Zagrosi Forge workflow hero" width="100%" />
</div>

Forge turns requests into bounded specs, plans, and tested code.
Lean mode is default: fewer files, shorter prompts, one setup, one final gate.

## Lean By Default

| Mode | Use |
|------|-----|
| `lean` | Default. Minimum sufficient artifacts and focused checks. |
| `standard` | Explicit opt-in for wider research, traceability, or coordination. |
| `deep` | Explicit opt-in for high-risk or architecture-heavy work. |

Depth changes research and review rigor. Compact contracts retain requirement IDs,
ownership, acceptance, and verification. No minimum prose quotas or duplicate ledgers.

## Install

Requires Python 3.11+ and host plugin support. Both hosts share all skills,
depths, and detached workflows.

### Claude Code

```bash
claude plugin marketplace add zagrosi-code/zagrosi-forge
claude plugin install zagrosi-forge@zagrosi
```

Restart after installation. Update with
`claude plugin marketplace update zagrosi`, then
`claude plugin update zagrosi-forge@zagrosi`.

Try a local checkout:

```bash
claude --plugin-dir "/absolute/path/to/zagrosi-forge"
```

### Codex

```bash
git clone https://github.com/zagrosi-code/zagrosi-forge.git
cd zagrosi-forge
python3 scripts/zagrosi_skills.py install --pretty
```

Restart Codex. Preview: `install --dry-run --pretty`; compare:
`update-check --pretty`; refresh: `self-update --pretty`.

Updates exclude development artifacts and preserve a recoverable installation.
Cache cleanup does not shrink the repository.

Or use Codex's marketplace:

```bash
codex plugin marketplace add zagrosi-code/zagrosi-forge
codex plugin add zagrosi-forge@zagrosi
```

## Use

Start at the phase you need. In Claude Code:

```text
/zagrosi-forge:zagrosi-project @planning/requirements.md
/zagrosi-forge:zagrosi-plan @planning/01-auth/spec.md
/zagrosi-forge:zagrosi-implement @planning/01-auth/sections/
```

In Codex:

```text
Use $zagrosi-forge:zagrosi-project on @planning/requirements.md
Use $zagrosi-forge:zagrosi-plan on @planning/01-auth/spec.md
Use $zagrosi-forge:zagrosi-implement on @planning/01-auth/sections/
```

<img src="assets/readme-workflow.svg" alt="Zagrosi Forge artifact workflow" width="100%" />

| Workflow | Required output |
|----------|-----------------|
| Project | `project-manifest.md` and child `spec.md` files |
| Single-section plan | `sections/index.md` and one canonical section containing tests, evidence, decisions, risks, and review |
| Multi-section plan | One shared plan and ordered sections that link shared contracts |
| Implement | Tests, code, and compact machine-readable section records |

Each phase uses setup and strict postflight:

```bash
python3 scripts/zagrosi_skills.py plan-setup \
  --file planning/01-auth/spec.md --plugin-root . --depth lean
# Complete the generated draft and review it; the source brief stays unchanged.
python3 scripts/zagrosi_skills.py postflight \
  --phase plan --planning-dir planning/01-auth --depth lean --strict
```

Project uses `project-setup`; implementation uses `implement-setup`.
Follow returned command arguments with actual evidence.
`status --path PATH --pretty` resumes work;
`commands --pretty` lists all commands.

The [compact-plan format](skills/zagrosi-plan/references/plan-format.md) maps source,
behavior, expected results and verification once per requirement.
`plan-setup --for-detached` retains physical contracts and separate records.

## Context and Performance

Context preserves complete sections, linked decisions/risks, relevant requirements,
and explicit omissions. `context-brief` and `implementation-packet` default to
2,000 words (`--max-words`). Broken links and oversized contracts fail explicitly.
Setup and recording include the next packet; successor failures preserve saved records.

Failed flights link complete reports with unique diagnostics.
`--full-output` returns the nested payload; no findings are discarded.

Progress identifies the next action. Records bind requirements and dependencies;
contract edits reopen completion, code drift requires integration checks.
Legacy records remain readable, labeled unbound.

The shared [engineering standard](skills/zagrosi-implement/references/engineering.md)
draws on [Ponytail](https://github.com/DietrichGebert/ponytail): trace callers, reuse code,
prefer clear names and direct flow. Repair encountered duplication and mixed responsibilities;
update ownership. Characterize behavior, run targeted regression checks, then the full suite.

macOS/Linux read-only gates avoid repeated process startup; other gates retain
isolation and timeouts. Compare checkouts:

```bash
python3 tools/benchmark_forge.py \
  --original-root /path/to/main --baseline-root /path/to/earlier-snapshot --runs 5
```

Benchmarks measure helper latency, context size and modeled reference loads across
depths on identical fixtures: [recorded comparison](examples/evals/performance.json).

[Coding trials](examples/evals/coding/README.md) retain failures, timings and usage.
Behavior, workflow and independent cleanup verdicts bind candidate, baseline, plugin and evaluator.

## Runtime

The CLI loads modules on demand. SHA-256 binds runtime/test sources; verification
compiles those exact bytes without bytecode caches. Detached processes reverify
sources, ownership, locks and frozen inputs. Analysis is reused within commands;
writes are serialized and atomic. Privileged policy uses a source-bound adapter.

Codex installs only `.codex-plugin/package-files.json` members, preserving unrelated
settings with locked, atomic updates. Claude Code manages its installation.
Forge adds no hooks, MCP servers, or background processes.

## Compatibility

`fast` remains a compatibility alias for `lean`. Existing `zagrosi-*`,
`deep-*`, `DEEP_META`, and migrated `claude-*` workflows remain accepted.
Reviews use the active agent; `codex_review` aliases `agent_review`, without
requiring another model CLI. Migrate old artifacts:

```bash
python3 scripts/zagrosi_skills.py migrate --planning-dir planning/01-auth
```

## Package Map

```text
skills/                    three shared Codex and Claude Code workflows
.claude-plugin/            Claude Code plugin and marketplace metadata
.codex-plugin/             Codex metadata and shared package inventory
scripts/zagrosi_skills.py  verified entrypoint and runtime manifest
scripts/forge/             focused CLI, workflow, and security modules
scripts/deep_skills.py     compatibility wrapper
examples/                  valid, invalid, and benchmark fixtures
tests/                     focused CLI, gate, recovery, and coding-trial tests
tools/                     runtime binding, performance comparison, coding trials
assets/                    icon and README visuals
```

## Validate

After runtime/test edits, run `python3 tools/update_runtime_manifest.py`.
After adding/removing packaged files, stage the intended files and run
`python3 tools/update_package_manifest.py`.
CI checks bindings and the full suite on Linux/Python 3.12; focused checks cover
Python 3.11, macOS and Windows.

```bash
python3 tools/update_runtime_manifest.py --check
python3 tools/update_package_manifest.py --check
uv run --with pytest python -m pytest
python3 scripts/zagrosi_skills.py doctor --plugin-root . --strict --pretty
python3 scripts/zagrosi_skills.py release-check --plugin-root . --pretty
plugin-scanner verify .
claude plugin validate . --strict
```

Zagrosi Forge is MIT licensed and includes attribution in [NOTICE.md](NOTICE.md).
