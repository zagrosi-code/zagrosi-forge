---
name: zagrosi-implement
description: Implement Zagrosi Forge section files with test-first development, focused review, documentation updates, and git hygiene. Use when the user has a sections directory from Zagrosi Plan and wants Codex to build the planned work incrementally with TDD and code review.
---

# Zagrosi Implement

Implement `$zagrosi-plan` section files one at a time. The workflow prioritizes
small tested changes, review before commit, and resumable progress.

## First Actions

1. Require a `sections/` directory containing `index.md`.
2. Resolve `plugin_root` as the nearest parent containing `scripts/zagrosi_skills.py`.
3. Determine `target_dir`, the repo where code should be written. Use the
   current working directory unless the user specifies another path or a
   previous config exists.
4. Run setup:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py implement-setup --sections-dir "{sections_dir}" --target-dir "{target_dir}"
```

This emits a `preflight` report automatically. Treat protected-branch and dirty
working-tree warnings like Pierce-style preflight warnings: pause for user
approval before editing when they affect the target repository.
When showing the command for a human to run outside Codex, add `--pretty`.

5. Parse the JSON. If `success` is false, show the error and stop.
6. If setup warns about a protected branch or dirty working tree, ask whether
   to continue before editing.
7. Run section readiness gates:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py lint-sections --planning-dir "{planning_dir}"
python3 {plugin_root}/scripts/zagrosi_skills.py traceability --planning-dir "{planning_dir}"
python3 {plugin_root}/scripts/zagrosi_skills.py lint-implementation-readiness --planning-dir "{planning_dir}"
python3 {plugin_root}/scripts/zagrosi_skills.py next-section --planning-dir "{planning_dir}"
```

8. Use `update_plan` when available with one milestone group per section:
   implement, review, fix, document, commit, record.

## Section Loop

For each incomplete section in `SECTION_MANIFEST` order:

### 1. Read

Read the section file and `sections/index.md`. If the section depends on
earlier sections, verify those are recorded complete or already present in the
codebase.

For large plans, generate a focused packet before implementation:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py implementation-packet --planning-dir "{planning_dir}" --section "{section}"
python3 {plugin_root}/scripts/zagrosi_skills.py context-brief --planning-dir "{planning_dir}" --section "{section}"
```

If the next section is too broad or owns too many files, ask Forge for a split
proposal before writing tests:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py suggest-section-splits --planning-dir "{planning_dir}"
```

### 2. Test First

Follow `references/tdd-review-git.md`.

Create or update tests before implementation. Run the targeted test command and
confirm the expected failure. If the failure is not meaningful, fix the test
before writing production code.

If helpful, generate throwaway red-test skeletons from the TDD plan:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py tdd-skeletons --planning-dir "{planning_dir}" --framework pytest
```

### 3. Implement

Make the smallest coherent implementation for the section. Follow existing repo
patterns. Do not refactor unrelated code.

Run targeted tests, then the section's configured `test_command` when practical.
Iterate until tests pass or a real blocker is clear.

Record milestones for long sections:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py implement-progress --planning-dir "{planning_dir}" --section "{section}" --stage red --result "targeted test failed as expected"
```

### 4. Stage and Review

If this is a git repo, stage only files related to the section. Save the diff to:

```text
{planning_dir}/implementation/code_review/{section}-diff.md
```

Validate staged or saved scope against the section:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py patch-scope --section-file "{section_file}" --repo "{target_dir}" --staged
python3 {plugin_root}/scripts/zagrosi_skills.py implementation-drift --planning-dir "{planning_dir}" --repo "{target_dir}" --staged --strict
```

Run an adversarial review. If subagents are explicitly authorized, delegate a
bounded review task with the section file and diff path; otherwise perform the
review locally.

Write:

```text
{planning_dir}/implementation/code_review/{section}-review.md
```

### 5. Triage Review

Fix correctness, security, data loss, and requirement-mismatch issues before
continuing. Ask the user only about product choices, tradeoffs, or changes that
alter scope.

Record decisions in:

```text
{planning_dir}/implementation/code_review/{section}-decisions.md
```

### 6. Update Section Documentation

Before committing, update the section file so it reflects what was actually
built. Use `references/section-update.md`.

### 7. Commit

If in a git repo, commit the section's implementation, tests, review artifacts
that live inside the repo, and section doc updates that live inside the repo.

Use the repo's commit style. Never bypass hooks unless the user explicitly
instructs it after seeing the failure.

If the repo has no clear commit style, generate a section-based draft:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py commit-message --section-file "{section_file}"
```

### 8. Record

Record completion:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py implement-record-section \
  --sections-dir "{sections_dir}" \
  --section "{section}" \
  --commit "{commit_hash_or_none}" \
  --notes "{short_note}"
```

Then continue to the next section.

## Completion

After all sections are recorded complete:

1. Run the full configured `test_command`.
2. Write `{planning_dir}/implementation/usage.md` with practical usage notes.
3. Run final gates:

```bash
python3 {plugin_root}/scripts/zagrosi_skills.py lint-implementation-state --sections-dir "{sections_dir}"
python3 {plugin_root}/scripts/zagrosi_skills.py postflight --phase implement --planning-dir "{planning_dir}" --sections-dir "{sections_dir}" --target-dir "{target_dir}" --staged
python3 {plugin_root}/scripts/zagrosi_skills.py status --path "{planning_dir}"
python3 {plugin_root}/scripts/zagrosi_skills.py report --planning-dir "{planning_dir}"
```

4. Summarize completed sections, commits, review files, tests run, and any
   residual risks.

Do not push or open a PR unless the user asks.
