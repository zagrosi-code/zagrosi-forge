<div align="center">
  <img src="assets/icon.svg" alt="Zagrosi Forge icon" width="96" height="96" />
  <h1>Zagrosi Forge</h1>
  <p><strong>Lean Codex workflows for project decomposition, planning, and test-first implementation.</strong></p>
  <p>
    <a href="https://github.com/zagrosi-code/zagrosi-forge"><img alt="Codex plugin" src="https://img.shields.io/badge/Codex-plugin-0F766E?style=flat-square" /></a>
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

Depth changes investigation and review rigor. Every mode uses compact contracts
with stable requirement IDs, file ownership, acceptance, and verification.
No minimum prose quotas or duplicate research, TDD, decision, and review files.

## Install

Requires Codex plugin support and Python 3.11+.

```bash
git clone https://github.com/zagrosi-code/zagrosi-forge.git
cd zagrosi-forge
python3 scripts/zagrosi_skills.py install --pretty
```

Restart Codex after success. Preview with `install --dry-run --pretty`; compare
the installed cache with `update-check --pretty`; refresh it with
`self-update --pretty`.

Updates exclude development artifacts and preserve a recoverable installation.
Cache cleanup does not shrink the repository.

Codex marketplace install is also supported:

```bash
codex plugin marketplace add zagrosi-code/zagrosi-forge
codex plugin add zagrosi-forge@zagrosi
```

## Use

Start at the phase you need:

```text
Use $zagrosi-forge:zagrosi-project on @planning/requirements.md
Use $zagrosi-forge:zagrosi-plan on @planning/01-auth/spec.md
Use $zagrosi-forge:zagrosi-implement on @planning/01-auth/sections/
```

<img src="assets/readme-workflow.svg" alt="Zagrosi Forge artifact workflow" width="100%" />

Output stays small at every depth:

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
Setup returns applicable command arguments. Replace evidence placeholders with
actual results; `status --path PATH --pretty` resumes work.
`commands --pretty` lists all commands.

Use the explicit [compact-plan format](skills/zagrosi-plan/references/plan-format.md).
Map source, behavior, expected results and verification once per requirement.
Legacy plans and reviews remain supported. Use `plan-setup --for-detached` for
detached frozen runs, which retain physical contracts and separate records.

## Context and Performance

Context preserves complete sections and linked decisions/risks, selects relevant
requirements, and identifies omissions. `context-brief` and `implementation-packet`
default to 2,000 words; adjust `--max-words` when necessary. Broken links and
oversized contracts fail explicitly. Setup and successful section recording
include the next packet. A saved record remains saved if its successor needs repair.

Failed flights return unique diagnostics and a path to the complete report.
Use `--full-output` for the nested machine payload; no findings are discarded.

Saved progress identifies the next action; changed inputs require fresh checks.
Records bind requirements and dependencies. Contract edits reopen completion;
code drift requires integration checks. Legacy records remain readable, labeled unbound.

The shared [engineering standard](skills/zagrosi-implement/references/engineering.md)
draws on [Ponytail](https://github.com/DietrichGebert/ponytail): trace callers, reuse
code, prefer clear names and direct flow. Repair encountered duplication and mixed
responsibilities; update ownership before broader edits. Characterize weak coverage,
run targeted regression checks, then the full suite at integration.

On macOS/Linux, known read-only gates avoid repeated process startup; other gates
retain process isolation and timeouts. Compare stable local checkouts:

```bash
python3 tools/benchmark_forge.py \
  --original-root /path/to/main --baseline-root /path/to/earlier-snapshot --runs 5
```

Benchmarks measure helper latency, context size and modeled reference loads on
identical fixtures across depths. See the
[recorded comparison](examples/evals/performance.json).

[Coding trials](examples/evals/coding/README.md) compare complete tasks, retaining
failures, timings and available usage. Verdicts separate behavior, workflow and
independent cleanup review, bound to candidate, baseline, plugin and evaluator.

## Runtime

The CLI imports modules on demand. SHA-256 manifests bind runtime/test sources;
verification compiles those exact bytes without bytecode caches. Detached runs
reverify sources, ownership, locks and immutable inputs in isolated processes.
Checks reuse unchanged analysis within one command. Mutable writes are serialized
and atomic. Privileged policy uses a source-bound adapter with frozen checks.

Installed files come from `.codex-plugin/package-files.json`; undeclared local
files stay out. Configuration updates preserve unrelated values, publish
atomically under a lock, and preview only Forge-owned settings.

## Compatibility

`fast` remains a compatibility alias for `lean`. Existing `zagrosi-*`,
`deep-*`, `DEEP_META`, and migrated `claude-*` workflows remain accepted. New
work should use the names above. Migrate recognized old artifacts with:

```bash
python3 scripts/zagrosi_skills.py migrate --planning-dir planning/01-auth
```

## Package Map

```text
skills/                    three Codex workflows
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
```

Zagrosi Forge is MIT licensed and includes attribution in [NOTICE.md](NOTICE.md).
