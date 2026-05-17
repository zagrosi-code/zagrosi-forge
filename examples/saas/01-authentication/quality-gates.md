# Quality Gates

Run these commands before implementation starts:

```text
python3 scripts/zagrosi_skills.py lint-plan --planning-dir examples/saas/01-authentication --depth standard --strict
python3 scripts/zagrosi_skills.py lint-sections --planning-dir examples/saas/01-authentication --depth standard --strict
python3 scripts/zagrosi_skills.py traceability --planning-dir examples/saas/01-authentication --strict
```

Run these commands during implementation in a real target repository:

```text
uv run pytest tests/auth/test_oauth.py
uv run pytest
```

## Gate Expectations

The plan gate should find a normalized spec, implementation plan, TDD plan,
governance files, requirement IDs, security detail, migration or compatibility
notes, rollout, rollback, and acceptance criteria. The section gate should find
a valid section index, dependency graph, execution order, parallelization
notes, concrete file paths, tests-first instructions, implementation details,
risks, and acceptance criteria. The traceability gate should prove REQ-001 is
covered in plan, TDD, and section artifacts.
