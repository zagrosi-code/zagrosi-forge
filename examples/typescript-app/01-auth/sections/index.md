<!-- PROJECT_CONFIG
runtime: node-vitest
test_command: npm test
END_PROJECT_CONFIG -->

<!-- SECTION_MANIFEST
section-01-auth-flow
section-02-preferences
END_MANIFEST -->

# Sections

## Project Notes

Runtime is Node with Vitest. Tests must be written before implementation. The
auth flow and settings flow are separated so the first section can create a
single session boundary and the second section can reuse it. Keep route handlers
thin and keep token values out of logs.

## Dependency Graph

| Section | Depends On | Blocks | Parallelizable |
|---------|------------|--------|----------------|
| section-01-auth-flow | - | section-02-preferences | No |
| section-02-preferences | section-01-auth-flow | - | No |

## Execution Order

1. Batch 1: section-01-auth-flow.
2. Batch 2: section-02-preferences after session helpers exist.

## Section Summaries

### section-01-auth-flow

Owns OAuth callback tests, callback parsing, state validation, provider denial
handling, replay handling, and signed session creation through
`src/auth/session.ts`.

### section-02-preferences

Owns settings validation and authenticated preference updates. It depends on the
session lookup created by section-01-auth-flow and must not create a second auth
boundary.

## Batch Detail

Batch 1 is intentionally isolated. It can be built without account settings
because it owns only callback behavior and the session helper surface. The
implementer should create red callback tests, add minimal callback and session
stubs, make the tests fail for behavioral reasons, then implement state
validation, provider-denial handling, replay rejection, and session creation.
The section is complete only when callback tests and the full configured test
command pass.

Batch 2 starts only after Batch 1 has established a session lookup helper. It
creates the settings preference module and tests that prove unauthenticated
requests cannot write. This sequence prevents a common planning failure where
settings code creates its own ad hoc auth check. The dependency is not just a
file dependency; it is a contract dependency on a single session boundary.

## Parallelization Notes

These sections should not be implemented in parallel unless two workers agree
on the exact `src/auth/session.ts` interface first. Both sections touch or read
that module, so parallel changes would otherwise create avoidable merge
conflicts and behavior drift. If parallel work is required, section 1 owns
session creation while section 2 may draft tests against an agreed lookup
signature but must wait to wire implementation until the helper exists.

## Verification Notes

The configured command is `npm test`. During implementation, targeted Vitest
runs are useful, but section completion requires the full command. If the target
repository also has `npm run lint` or `npm run typecheck`, run those before
recording completion. Review artifacts should call out any skipped command and
why it was skipped.

## Risk Register

The main risk in section 1 is session creation on an invalid or denied callback.
The main risk in section 2 is a settings update path that bypasses auth. A
secondary risk in both sections is leaking tokens or cookies through logs,
assertion output, or error messages. Tests should use sentinel secret values so
log capture can prove the implementation keeps those values private.

## Handoff Standard

Each section is written for an implementer who has not read the original
conversation. The section file must copy the relevant requirements, contracts,
test names, file paths, result shapes, edge cases, and final verification
commands. It should not tell the implementer to go back to the plan for
essential context. Cross-references are acceptable for orientation, but the
implementation instructions inside each section need to stand alone.

When using multiple workers, assign ownership by file. `section-01-auth-flow`
owns `src/auth/callback.ts`, callback tests, and the session creation addition
in `src/auth/session.ts`. `section-02-preferences` owns
`src/settings/preferences.ts`, preference tests, and only the session lookup
surface it consumes. If section 2 needs a new session helper that section 1 did
not create, pause and reconcile the interface instead of adding a parallel auth
path.

## Completion Evidence

For each section, record the targeted test command, the full `npm test` result,
and any lint or type-check command discovered in `package.json`. Completion
notes should mention whether token log-capture assertions were included and
whether account-link ambiguity was handled by existing policy or left as a
structured stop-line error. A section is not implementation-ready if it lacks
tests-first instructions, concrete file paths, acceptance criteria, or risk
notes.

If repository paths differ, keep the section names and requirement ownership
stable while recording the adapted paths in the completion notes. The examples
use `src/auth` and `src/settings` because those are easy to read, but the real
quality bar is behavioral traceability: every implemented file should support
REQ-001 or REQ-002, and every test should explain which requirement it proves.
