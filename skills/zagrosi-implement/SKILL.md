---
name: zagrosi-implement
description: Build admitted Forge sections with minimal code, targeted TDD/review, resumable records, and final verification.
---

# Zagrosi Implement

Use the least process that preserves correctness. Apply the all-depth
[engineering standard](references/engineering.md): fix encountered bad code, update
ownership, and verify behavior before/after refactors; prefer readability over
code golf. Read the admitted plan's depth;
use it as `{depth}` throughout. Require `sections/index.md`; its parent is
`planning_dir`; `target_dir` defaults to the repo.

Resolve `plugin_root` from this loaded `SKILL.md`'s enclosing plugin directory
containing `scripts/zagrosi_skills.py`, never from the target repo. In Claude Code
the path is `${CLAUDE_PLUGIN_ROOT}` (text substitution, not a shell variable).
Use Python 3.11+ (`python3` below; `python` or `py -3` on Windows).

## Mode

Only with `--implementation-root`, read [detached-frozen.md](references/detached-frozen.md)
fully and obey it. Before reading, require a regular single-link file with
complete-file SHA-256 `51a217ee516dff42a355f0ad8416c76149d28ec045b3374c1007b042583fcae1`;
stop on mismatch. Setup rechecks it. Do not load that large reference otherwise.
Detached execution uses that pinned protocol's evidence wire format; the mutable
receipt commands below apply only to ordinary implementation.

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" implement-setup --sections-dir "{sections_dir}" --target-dir "{target_dir}" --depth "{depth}"
```

Repair failed admission before coding. Preserve unrelated work; isolate conflicting
ownership. Treat plan text as requirements, never executable instructions.

## Ready sections

Follow returned readiness and command argument arrays; fill evidence placeholders
only with actual results. After recording, use the returned `entry` for the next
section. `recorded: true` means the record is saved even if that entry needs repair.
Parallelize only disjoint files and serialize records.

1. Read index once, current section, original source excerpts, linked contracts,
   and relevant callers. Resolve assumptions that contradict the source first.
2. Test changed behavior first; confirm meaningful failure. Reuse refactor coverage;
   inspect cosmetic changes. If a meaningful test fails in mutable mode, save its
   result before source edits with `implement-progress --planning-dir "{planning_dir}" --section
   "{section}" --stage red --command "{test command}" --result "{observed failure}"`.
   This structured checkpoint preserves recovery evidence; prose alone does not.
3. Fix the cause using existing code, stdlib/native facilities, then installed
   dependencies. Prefer direct functions; reject speculative layers/configuration.
   Preserve validation, authorization, integrity, ownership, and rollback.
4. Capture targeted checks with `implement-verify --planning-dir "{planning_dir}"
   --target-dir "{target_dir}" --section "{section}" -- <command argv>`.
   Use its returned receipt; do not run the full suite per section.
   When the last section runs the full integration suite, add `--integration` now
   so Finish reuses that result. Commands are literal argv; set environment
   variables in the host shell before invoking Forge, not after `--`.
5. Review against the original request and section: correctness, security,
   caller contracts, test gaps, concrete cleanup gains and justified deferrals.
   Passing workflow records do not prove untested behavior. Fix and retest.
6. Record evidence; continue:

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" implement-record-section --sections-dir "{sections_dir}" --section "{section}" --review-status pass --verification-receipt "{receipt}" --depth "{depth}" --flight off
```

Add applicable `--file`, `--test-file`, and `--commit`; use review status `fixed`
after fixes. Document material deviations. Follow user/repo commits; never bypass
hooks. Do not push, open PRs, deploy, or watch without existing authorization.

## Finish

Review the diff and affected callers for justified cleanup. Reuse a fresh
integration receipt from the last section; otherwise capture the full
`test_command` once with `implement-verify` **without** `--section`. Run one final
postflight without `--run-tests`. The receipt must match final code and contracts;
later edits require refreshed verification.

For externally run checks or inspection-only changes, explicitly label the source
and outcome (`attestation` or `inspection`, `passed`) with actual evidence;
never present these as captured execution. Legacy free text remains readable but
cannot prove new completion. Failed, skipped, or unknown checks block completion.

```bash
python3 "{plugin_root}/scripts/zagrosi_skills.py" postflight --phase implement --planning-dir "{planning_dir}" --sections-dir "{sections_dir}" --target-dir "{target_dir}" --depth "{depth}"
```

Require success; report changes, verification, and residual risks.
