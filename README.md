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

Forge turns a broad request into bounded specs, an implementation-ready plan,
and tested code. Lean mode is default: fewer files, shorter prompts, one setup,
one final gate.

## Lean By Default

| Mode | Use |
|------|-----|
| `lean` | Default. Minimum sufficient artifacts and focused checks. |
| `standard` | Explicit opt-in for wider research, traceability, or coordination. |
| `deep` | Explicit opt-in for high-risk or architecture-heavy work. |

Depth changes investigation and review rigor. Every mode uses compact, canonical
contracts with stable requirement IDs, exact file ownership, observable acceptance,
and verification. No minimum prose quotas or duplicate research, TDD, decision,
and review files. Extra artifacts serve independent ownership or a material need.

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

Updates prune development artifacts before scanning and preserve a recoverable
installation during replacement. Cache cleanup does not shrink the repository.

Codex marketplace install is also supported:

```bash
codex plugin marketplace add zagrosi-code/zagrosi-forge
codex plugin add zagrosi-forge@zagrosi
```

## Use

Invoke the three skills in order, or start at the phase you need:

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

Each phase runs setup, performs the work, then runs strict postflight:

```bash
python3 scripts/zagrosi_skills.py plan-setup \
  --file planning/01-auth/spec.md --plugin-root . --depth lean
# Write and review the plan and sections.
python3 scripts/zagrosi_skills.py postflight \
  --phase plan --planning-dir planning/01-auth --depth lean --strict
```

Project uses `project-setup`; implementation uses `implement-setup`. Run
`commands --pretty` for the compact command catalog and
`status --path PATH --pretty` to resume.

Use the explicit [compact-plan format](skills/zagrosi-plan/references/plan-format.md).
Legacy plans and reviews remain supported. Detached frozen runs retain physical
contracts and separate operational records.

## Context and Performance

Context preserves complete sections and linked decisions/risks, selects relevant
requirements, and identifies omissions. `context-brief` and `implementation-packet`
default to 2,000 words; adjust `--max-words` when necessary. Broken links and
oversized contracts fail explicitly. Setup and `next-section` include the packet.

Saved progress supplies the resume stage and next action; changed inputs require
fresh checks. New completion records bind requirements and dependencies. Contract
edits reopen completion; code drift is reported for final integration because
later sections can edit shared files. Legacy records remain readable, labeled unbound.

The shared [engineering standard](skills/zagrosi-implement/references/engineering.md)
draws on [Ponytail](https://github.com/DietrichGebert/ponytail): trace callers, reuse
existing code and standard libraries, prefer clear names and direct flow. Repair
encountered duplication, oversized modules and mixed responsibilities; update
ownership before broader edits. Characterize weak coverage before refactoring,
run targeted regression checks, then the full suite at integration.

On macOS/Linux, known read-only gates avoid repeated process startup; other gates
retain process isolation and timeouts. Compare stable local checkouts:

```bash
python3 tools/benchmark_forge.py \
  --original-root /path/to/main --baseline-root /path/to/earlier-snapshot --runs 5
```

Benchmarks use identical fixtures across depths: source hashes, median helper
latency, context size and modeled reference loads. See the scoped
[recorded comparison](examples/evals/performance.json). Whole-task speed and model
usage require separate measurements.

[Coding trials](examples/evals/coding/README.md) cover features, cleanup, deep
planning, resume, multi-module CSV imports and native JavaScript. Repeated task
matrices retain failures, timings and available usage. Separate verdicts check behavior, workflow completion, and
independently reviewed cleanup. Unchanged fixtures cannot pass cleanup. Reviews
and provenance bind the candidate, baseline, plugin runtime, and evaluator.

## Runtime

The CLI imports modules on demand. SHA-256 manifests bind runtime/test sources;
verification compiles those exact bytes without bytecode caches. Detached runs
reverify sources, ownership, locks and immutable inputs with process isolation.
Checks reuse unchanged parsing and score components within one command; analyses
return structured results. Mutable writes are serialized and atomic. Privileged
project policy loads through a source-bound adapter with frozen checks.

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
