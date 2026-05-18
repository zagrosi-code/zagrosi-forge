# Zagrosi Forge

Codex-native skills for turning ambitious software ideas into researched plans,
test-first implementation sections, and reviewed code changes.

Zagrosi Forge packages three skills plus a deterministic helper CLI:

| Skill | Use It For | Output |
|-------|------------|--------|
| `$zagrosi-forge:zagrosi-project` | Breaking a broad project brief into focused planning units | `project-manifest.md` and split `spec.md` files |
| `$zagrosi-forge:zagrosi-plan` | Turning one spec into a reviewed TDD implementation plan | `codex-plan.md`, `codex-plan-tdd.md`, `sections/` |
| `$zagrosi-forge:zagrosi-implement` | Building section files with tests, review, docs, and git hygiene | completed sections, review artifacts, implementation state |

The workflows are resumable because state is inferred from files on disk. There
are no Claude-specific hooks, injected task files, or hidden session contracts.

## Why Forge

Zagrosi Forge is designed for complex work where ordinary prompt-and-code
loops become fragile:

- Broad requirements need decomposition before implementation.
- Plans need research, test strategy, traceability, and review.
- Implementation should happen in bounded sections, with scope checks and
  test-first discipline.
- Codex should spend its context on the next useful decision, not rebuilding
  process scaffolding every time.

The depth target is deliberately high. Strong planning artifacts should look
closer to a serious engineering design doc than a short checklist: multi-
thousand-word implementation plans, detailed TDD matrices, review integration,
and self-contained implementation sections.

## Installation

Zagrosi Forge is packaged as a Codex plugin with a local marketplace entry and
an installer that materializes Codex's plugin cache. You need Codex with plugin
support and Python 3.11 or newer. `uv` is optional, but useful for running the
test suite. No API keys are required.

Clone the repository and run the installer:

```bash
git clone https://github.com/zagrosi-code/zagrosi-forge.git
cd zagrosi-forge
python3 scripts/zagrosi_skills.py install --pretty
```

Or ask Codex to do the local install for you:

```text
Please install the Codex plugin from https://github.com/zagrosi-code/zagrosi-forge.
Clone it into a local projects directory, run its installer, preserve any
existing ~/.codex/config.toml settings, verify it with codex debug prompt-input,
and tell me when to restart Codex.
```

The installer validates the package, creates a timestamped backup of
`~/.codex/config.toml` when it changes, writes the local marketplace/plugin
entries, copies the package into Codex's plugin cache, and verifies the skills
with `codex debug prompt-input` when the `codex` CLI is available. Restart Codex
after it reports success. The canonical installed skill names are:

```text
$zagrosi-forge:zagrosi-project
$zagrosi-forge:zagrosi-plan
$zagrosi-forge:zagrosi-implement
```

Preview the config and cache changes without writing them:

```bash
python3 scripts/zagrosi_skills.py install --dry-run --pretty
```

Check whether Codex's installed plugin cache matches this local checkout:

```bash
python3 scripts/zagrosi_skills.py update-check --pretty
```

If the check reports stale cache or config, refresh the local install:

```bash
python3 scripts/zagrosi_skills.py self-update --pretty
```

Restart Codex when `self-update` reports changed cache or config. Forge does not poll git remotes automatically.
At session start, update the repository checkout yourself when you want newer
remote source, then run `update-check` or `self-update` to sync Codex's local
plugin cache.

Manual install is still possible if you prefer editing config yourself, but it
requires both config and cache state. Add the repo as a local marketplace in
`~/.codex/config.toml`:

```toml
[marketplaces.zagrosi]
source_type = "local"
source = "/absolute/path/to/zagrosi-forge"

[plugins."zagrosi-forge@zagrosi"]
enabled = true
```

Then copy the package into the versioned cache path shown in
`.codex-plugin/plugin.json`, for example:

```text
~/.codex/plugins/cache/zagrosi/zagrosi-forge/0.2.0/
```

For most users, the installer is the safer path because it keeps those pieces in
sync.

`codex plugin marketplace add zagrosi-code/zagrosi-forge` can register the
public GitHub marketplace, but by itself it does not enable or materialize the
plugin cache in current Codex CLI versions. Use the installer until Codex
exposes a complete non-interactive marketplace install command.

Quick verification:

```bash
python3 /absolute/path/to/zagrosi-forge/scripts/zagrosi_skills.py doctor \
  --plugin-root /absolute/path/to/zagrosi-forge \
  --pretty
```

You can also use the helper CLI without enabling the plugin, but Codex will not
auto-load the skills unless the plugin is enabled:

```bash
python3 /absolute/path/to/zagrosi-forge/scripts/zagrosi_skills.py status \
  --path planning/01-auth \
  --pretty
```

## Quick Start

Use the skills directly inside Codex:

```text
Use $zagrosi-forge:zagrosi-project to split this idea: improve billing, auth, and dashboard workflows for our SaaS app.
Use $zagrosi-forge:zagrosi-project on @planning/requirements.md
Use $zagrosi-forge:zagrosi-plan on @planning/01-auth/spec.md
Use $zagrosi-forge:zagrosi-implement on @planning/01-auth/sections/.
```

Typical flow:

```text
chat idea or requirements.md
  -> $zagrosi-forge:zagrosi-project
  -> project-manifest.md + split specs
  -> $zagrosi-forge:zagrosi-plan
  -> reviewed plan + TDD plan + section files
  -> $zagrosi-forge:zagrosi-implement
  -> tested implementation + review artifacts
```

## Helper CLI

The helper script is intentionally boring: it reads files, validates contracts,
prints JSON, and exits non-zero when a gate blocks progress.

```bash
python3 scripts/zagrosi_skills.py doctor
python3 scripts/zagrosi_skills.py install --dry-run --pretty
python3 scripts/zagrosi_skills.py status --path planning/01-auth
python3 scripts/zagrosi_skills.py lint-interview --phase plan --planning-dir planning/01-auth --strict
python3 scripts/zagrosi_skills.py preflight --phase plan --file planning/01-auth/spec.md
python3 scripts/zagrosi_skills.py postflight --phase plan --planning-dir planning/01-auth --depth standard
python3 scripts/zagrosi_skills.py plan --file planning/01-auth/spec.md --plugin-root . --pretty
```

Forge setup commands run phase-aware flights automatically by default, following
the same spirit as the Deep Trilogy setup reports: validate inputs, inspect
resume state, run the relevant gates, and return a visible JSON report.
Add `--pretty` anywhere in the command for a human-readable console report; omit
it for stable JSON output in Codex, scripts, and CI.

Control the automation with `--flight`:

| Mode | Behavior |
|------|----------|
| `auto` | Default. Block only gates that already fail at high/critical severity. |
| `strict` | Treat medium findings as blocking too. |
| `advisory` | Run the gates and report findings without blocking the wrapper. |
| `off` | Skip automatic flight reports. |

For command discovery, use the helper itself instead of reading the whole
argparse listing:

```bash
python3 scripts/zagrosi_skills.py commands --pretty
python3 scripts/zagrosi_skills.py commands --phase plan
```

Plan-aware status is available through `status`: run it after setup or during
resume to see the current planning directory, section progress,
`plan_artifacts`, and next action. Missing or empty artifacts do not advance the
next action.

### Project Commands

```bash
python3 scripts/zagrosi_skills.py project-setup --brief "Build a SaaS app with auth, billing, and dashboard workflows." --planning-dir planning/saas
python3 scripts/zagrosi_skills.py project-setup --file planning/requirements.md
python3 scripts/zagrosi_skills.py project --file planning/requirements.md
python3 scripts/zagrosi_skills.py project-create-dirs --planning-dir planning
python3 scripts/zagrosi_skills.py lint-interview --phase project --planning-dir planning --strict
python3 scripts/zagrosi_skills.py lint-project-manifest --planning-dir planning
```

### Planning Commands

```bash
python3 scripts/zagrosi_skills.py plan-setup --file planning/01-auth/spec.md --plugin-root .
python3 scripts/zagrosi_skills.py plan --file planning/01-auth/spec.md --plugin-root .
python3 scripts/zagrosi_skills.py plan-check-sections --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py plan-generate-section-prompts --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py lint-interview --phase plan --planning-dir planning/01-auth --strict
python3 scripts/zagrosi_skills.py lint-plan --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py lint-evidence --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py lint-artifact-schema --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py lint-sections --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py lint-implementation-readiness --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py lint-review-integration --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py traceability --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py forge-score --planning-dir planning/01-auth --depth standard
python3 scripts/zagrosi_skills.py report --planning-dir planning/01-auth --output reports/forge.html
```

### Implementation Commands

```bash
python3 scripts/zagrosi_skills.py implement-setup --sections-dir planning/01-auth/sections --target-dir .
python3 scripts/zagrosi_skills.py implement --sections-dir planning/01-auth/sections --target-dir .
python3 scripts/zagrosi_skills.py implementation-packet --planning-dir planning/01-auth --section section-01-auth
python3 scripts/zagrosi_skills.py context-brief --planning-dir planning/01-auth --section section-01-auth
python3 scripts/zagrosi_skills.py tdd-skeletons --planning-dir planning/01-auth --framework pytest
python3 scripts/zagrosi_skills.py next-section --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py suggest-section-splits --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py implementation-drift --planning-dir planning/01-auth --diff-file section.diff --strict
python3 scripts/zagrosi_skills.py patch-scope --section-file planning/01-auth/sections/section-01-auth.md --staged
python3 scripts/zagrosi_skills.py commit-message --section-file planning/01-auth/sections/section-01-auth.md
python3 scripts/zagrosi_skills.py implement-progress --planning-dir planning/01-auth --section section-01-auth --stage verified
python3 scripts/zagrosi_skills.py implement-record-section --sections-dir planning/01-auth/sections --section section-01-auth
python3 scripts/zagrosi_skills.py lint-implementation-state --sections-dir planning/01-auth/sections
```

`implement-record-section` updates implementation state and refreshes
`traceability.md` so requirement rows move from planned to partial or
implemented as sections are recorded.

Old `zagrosi-*` and upstream-style `deep-*` helper command names are kept as
aliases for compatibility. New documentation uses the shorter Forge command
names.

## Quality Gates

Forge gates produce a score, machine-readable findings, and a success flag.
Profiles tune severity weighting for different environments:

| Profile | Bias |
|---------|------|
| `solo` | Balanced defaults for personal or small-project work |
| `startup` | Scope control and speed with practical testing |
| `enterprise` | Stronger security, traceability, migration, and readiness |
| `regulated` | Maximum traceability, privacy, security, and rollback rigor |
| `oss-maintainer` | Reviewability, test coverage, and contributor-friendly scope |

Use `--strict` to make medium findings block:

```bash
python3 scripts/zagrosi_skills.py lint-plan \
  --planning-dir planning/01-auth \
  --profile enterprise \
  --strict
```

Depth modes also control artifact-size expectations:

| Artifact | Fast | Standard | Deep |
|----------|------|----------|------|
| implementation plan | 900+ words | 2,500+ words | 5,000+ words |
| TDD plan | 450+ words | 1,200+ words | 2,000+ words |
| implementation section | 250+ words | 1,000+ words | 1,500+ words |
| review file | 500+ words | 1,000+ words | 1,800+ words |

Standard and deep mode also check for reader orientation, current-state
evidence, architecture rationale, contracts, file trees, phase plans, test
matrices, and review integration.

Export findings as JSONL or SARIF:

```bash
python3 scripts/zagrosi_skills.py lint-sections \
  --planning-dir planning/01-auth \
  --export reports/sections.sarif \
  --export-format sarif
```

## Planning Intelligence

These helpers make the generated plans easier to operate:

```bash
python3 scripts/zagrosi_skills.py forge-score --planning-dir planning/01-auth --depth standard --strict
python3 scripts/zagrosi_skills.py lint-evidence --planning-dir planning/01-auth --strict
python3 scripts/zagrosi_skills.py lint-implementation-readiness --planning-dir planning/01-auth --strict
python3 scripts/zagrosi_skills.py lint-review-integration --planning-dir planning/01-auth --strict
python3 scripts/zagrosi_skills.py lint-artifact-schema --planning-dir planning/01-auth --strict
python3 scripts/zagrosi_skills.py codebase-evidence --target-dir . --planning-dir planning/01-auth --write
python3 scripts/zagrosi_skills.py report --planning-dir planning/01-auth --output reports/forge.html
python3 scripts/zagrosi_skills.py assumption-ledger --planning-dir planning/01-auth --write
python3 scripts/zagrosi_skills.py section-estimates --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py parallel-plan --planning-dir planning/01-auth
python3 scripts/zagrosi_skills.py plan-diff --before old/codex-plan.md --after new/codex-plan.md
python3 scripts/zagrosi_skills.py trace-export --planning-dir planning/01-auth --format csv --output trace.csv
python3 scripts/zagrosi_skills.py agent-prompts --planning-dir planning/01-auth --type all
python3 scripts/zagrosi_skills.py context-budget --planning-dir planning/01-auth --max-words 12000
python3 scripts/zagrosi_skills.py e2e-trial-record --planning-dir planning/01-auth --name first-real-repo-pass
python3 scripts/zagrosi_skills.py eval-suite --examples-dir examples --check-snapshots --output examples/evals/latest.json
python3 scripts/zagrosi_skills.py release-check --plugin-root .
```

Forge also checks for vague section names, oversized sections, missing file
paths, orphaned requirements, missing rollback coverage, and implementation
scope drift.

### Forge Score

`forge-score` rolls the major gates into one release-style score:

| Component | Meaning |
|-----------|---------|
| plan depth | plan/spec/TDD depth and required planning facets |
| section readiness | section completeness, dependencies, files, and risks |
| traceability | REQ-* coverage through plan, TDD, and sections |
| evidence quality | codebase evidence, commands, runtime/test discovery, assumptions |
| implementation readiness | tests-first clarity, contracts, rollback, ownership |

Scores are still diagnostic, not a substitute for judgment. A score below 90
means the plan should usually be improved before implementation.

Use `--write-history` to append score history under
`{planning_dir}/.forge/scores/history.jsonl`. The next run reports
`trend_delta`, plus separate `blocking_score` and `advisory_score` values so
teams can distinguish release blockers from cleanup work.

### Drift And Resume Safety

Forge assumes Codex may compact context or resume later. The durable artifacts
are therefore the source of truth:

| Command | Purpose |
|---------|---------|
| `codebase-evidence --write` | Writes expanded codebase evidence: runtime files, source files, tests, skills, plugin metadata, CI, examples, eval metadata, and candidate commands |
| `lint-artifact-schema` | Verifies governance tables are machine-readable |
| `implementation-drift` | Compares changed files against planned section ownership |
| `suggest-section-splits` | Proposes smaller sections when ownership or word count gets too large |
| `report` | Writes a local HTML score and traceability report |
| `e2e-trial-record` | Records real-world planning/implementation trial metrics |
| `release-check` | Runs the repo-level validator bundle before publishing |

Expanded codebase evidence is bounded and content-free: it records relative
paths and inferred commands, not source contents. `eval-suite` uses
`examples/evals/suite.json` when present, can check golden snapshots with
`--check-snapshots`, and only changes snapshots when a maintainer intentionally
runs `--update-snapshots`. `release-check --plugin-root .` includes the
snapshot gate and never updates snapshots.

### Flight Gates

`preflight` and `postflight` expose the same phase-aware checks that setup
commands run automatically:

```bash
python3 scripts/zagrosi_skills.py preflight --phase project --brief "Build a SaaS app with auth, billing, and dashboard workflows." --planning-dir planning/saas
python3 scripts/zagrosi_skills.py preflight --phase project --file planning/requirements.md
python3 scripts/zagrosi_skills.py postflight --phase project --planning-dir planning

python3 scripts/zagrosi_skills.py preflight --phase plan --file planning/01-auth/spec.md --target-dir .
python3 scripts/zagrosi_skills.py postflight --phase plan --planning-dir planning/01-auth --depth standard --strict

python3 scripts/zagrosi_skills.py preflight --phase implement --sections-dir planning/01-auth/sections --target-dir .
python3 scripts/zagrosi_skills.py postflight --phase implement --planning-dir planning/01-auth --sections-dir planning/01-auth/sections --staged

python3 scripts/zagrosi_skills.py preflight --phase release --plugin-root .
python3 scripts/zagrosi_skills.py postflight --phase release --plugin-root . --run-tests
```

The reports include gate names, commands, parsed JSON payloads, blocking gate
names, git warnings where relevant, and the phase/stage that produced them.
Use `--pretty` on these commands when running them by hand.

## Artifact Contract

Durable planning artifacts use a `FORGE_META` block near the top:

```markdown
<!-- FORGE_META
{
  "artifact_type": "implementation_plan",
  "workflow": "zagrosi-plan",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001"]
}
END_FORGE_META -->
```

Legacy `DEEP_META` blocks are still accepted for migrated artifacts.

## Examples

The repository includes fixtures for both happy paths and failing gates:

| Path | Purpose |
|------|---------|
| `examples/saas/` | Python-style SaaS planning fixture |
| `examples/typescript-app/` | TypeScript app fixture with two dependent sections |
| `examples/deep-review/` | Deep-mode migration scenario starter for review-board benchmarking |
| `examples/gallery/` | Scenario starters for Next.js, FastAPI, Rails, Go, data migration, and AI-agent projects |
| `examples/evals/` | Benchmark metadata, golden Forge Score snapshots, and eval-suite output location |
| `examples/invalid/` | Negative fixtures for missing evidence, bad governance tables, oversized sections, vague sections, and absent section indexes |

The valid examples are intentionally large enough to clear `standard --strict`
gates. Invalid examples stay short because they prove gate failures.

## Domain Packs

`$zagrosi-forge:zagrosi-plan` can load focused reference packs when a spec enters a common
risk area:

| Reference | Use For |
|-----------|---------|
| `domain-auth.md` | auth, sessions, OAuth, identity, permissions |
| `domain-frontend.md` | UI workflows, forms, browser states |
| `domain-payments.md` | billing, checkout, webhooks, entitlements |
| `domain-data-migration.md` | schemas, backfills, expand/contract work |
| `domain-ai-products.md` | LLM workflows, tools, evals, safety |
| `domain-infra.md` | deployment, secrets, observability, operations |

## Migration

For old Claude-style artifacts:

```bash
python3 scripts/zagrosi_skills.py migrate --planning-dir planning/01-auth
```

The migrator copies recognized `claude-*.md` artifacts to `codex-*.md` names and
adds Forge governance stubs when useful.

## Package Layout

```text
.agents/plugins/marketplace.json  local Codex marketplace entry
.codex-plugin/plugin.json       Codex plugin manifest
skills/zagrosi-project/         project decomposition skill
skills/zagrosi-plan/            planning and sectioning skill
skills/zagrosi-implement/       implementation skill
scripts/zagrosi_skills.py       deterministic helper CLI
scripts/deep_skills.py          backward-compatible wrapper
examples/                       valid and invalid workflow fixtures
tests/                          CLI and gate tests
```

## Validation

```bash
uv run --with pytest python -m pytest
uv run --with pyyaml python /Users/zagrosi/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/zagrosi-project
uv run --with pyyaml python /Users/zagrosi/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/zagrosi-plan
uv run --with pyyaml python /Users/zagrosi/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/zagrosi-implement
python3 -m json.tool .codex-plugin/plugin.json >/dev/null
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
```

## License And Attribution

Zagrosi Forge is MIT licensed. It is a Codex-native redesign inspired by Pierce
Lamb's MIT-licensed Deep Trilogy projects. See [NOTICE.md](NOTICE.md).
