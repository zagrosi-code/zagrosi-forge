# Quality Gates

Run these Forge gates before treating the example as implementation-ready:

```text
python3 scripts/zagrosi_skills.py lint-interview --phase plan --planning-dir examples/typescript-app/01-auth --strict
python3 scripts/zagrosi_skills.py lint-plan --planning-dir examples/typescript-app/01-auth --depth standard --strict
python3 scripts/zagrosi_skills.py lint-sections --planning-dir examples/typescript-app/01-auth --depth standard --strict
python3 scripts/zagrosi_skills.py traceability --planning-dir examples/typescript-app/01-auth --strict
```

Run these target-repository commands during implementation:

```text
npm test -- src/auth/callback.test.ts
npm test -- src/settings/preferences.test.ts
npm test
```

## Gate Expectations

The plan gate should prove the spec, implementation plan, TDD plan, governance
files, security notes, migration or compatibility notes, rollout, rollback,
and acceptance criteria are present. The section gate should prove the section
index and both section files include dependencies, execution order,
parallelization, tests-first guidance, file paths, implementation contracts,
risks, and acceptance criteria. Traceability should prove REQ-001 and REQ-002
are covered by plan, TDD, and section artifacts.
