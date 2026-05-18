# Review Integration

## Accepted Review Items

- Keep auth flow and preferences as separate implementation sections.
- Preserve the dependency from preferences to authenticated user state.
- Require Vitest coverage for both login/session behavior and preference persistence.
- Keep traceability explicit for REQ-001 and REQ-002.

## Plan Updates

The section index and section files already reflect these review items. No scope expansion is accepted beyond auth flow and preferences persistence.

## Rationale

The review confirmed that merging auth flow and preferences into one broad section would hide the dependency that matters most: preferences need a stable authenticated user contract. The accepted plan keeps auth behavior first, then lets preferences consume that result. This sequencing makes failures easier to localize and gives the implementer a clear rollback point if session behavior is not ready.

The review also rejected a shortcut where preferences tests would seed user identity directly inside preference fixtures. That would make the tests faster to write, but it would not validate the integration boundary that the application relies on. The section files should therefore copy enough context from the auth-flow contract for preference tests to use the same state path as runtime code.

The final plan keeps implementation scope narrow. It does not add billing, profile management, team permissions, or unrelated settings UI. Verification remains `npm test`, with focused Vitest cases for REQ-001 auth behavior and REQ-002 preferences persistence before any broader checks.

## Integrated Changes

The plan keeps section 01 focused on the auth state contract. It should produce deterministic behavior for successful login, invalid credentials, session restore, and logout cleanup. The preferences section must treat that contract as its dependency instead of re-creating user state in preference fixtures. This was accepted because it improves traceability from REQ-001 to REQ-002 and prevents hidden coupling between tests.

The review also clarified that preferences persistence is user-scoped data. The section file should require unauthenticated writes to fail and should require saved preferences to be associated with the current user identity. Preference defaults, invalid payload behavior, and persistence failure behavior are included as acceptance details because they determine whether the feature is reliable under normal application use.

No accepted review item changes the original scope. The fixture still excludes billing, profile management, authorization roles, team settings, and unrelated UI polish. The only accepted changes are planning clarifications that make implementation safer and easier to audit.

## Verification Impact

The verification command remains `npm test`. Focused implementation can run only the auth-flow or preferences Vitest tests while a section is in progress, but final verification should run the configured command. Traceability remains explicit: REQ-001 is covered by auth-flow plan, TDD, section, and tests; REQ-002 is covered by preferences plan, TDD, section, and tests.

## Rejected Review Items

The review rejected combining the two sections for speed. That would reduce artifact count, but it would also remove the explicit dependency between authenticated user state and preferences persistence. The planning record keeps the dependency visible because a later implementer may use only the section files and traceability matrix to understand sequencing.

The review rejected preference tests that seed user identity directly in preference fixtures. Those tests can pass while the real application still cannot connect saved preferences to the active session. The accepted approach requires preferences tests to consume the same auth state contract established in section 01.

The review also rejected broad UI polish in this planning unit. Styling, navigation copy, settings page layout, and profile management are outside the requirement. Keeping them out of scope protects the test plan from becoming a vague acceptance checklist and keeps implementation review focused on state and persistence.

## Durable Record Notes

This integration file is part of the durable Forge planning record. It explains why the final section split and test strategy were accepted, which rejected shortcuts should not be revived during implementation, and which files should be consulted if work resumes after context compaction. Future implementers should treat these notes as binding planning context unless the user explicitly changes scope.

## Section Guidance

Section 01 should keep the auth contract small and observable. It should expose whether a user is authenticated, what user identity is active, how login failures are represented, how session restoration behaves, and how logout clears state. Those details are enough for section 02 to consume without adding unrelated auth features.

Section 02 should consume the auth contract rather than own it. It should load preferences for the active user, apply documented defaults, validate changes before saving, reject unauthenticated writes, and report persistence failures in a stable shape. It should not add role management, account settings, or profile editing.

The accepted sequence is therefore: write red auth-flow tests, implement auth-flow behavior, verify auth-flow tests, record section 01, then write red preferences tests that use the auth state contract, implement preferences behavior, verify preferences tests, record section 02, and run the full `npm test` command.

This sequencing is part of the plan, not optional advice. It preserves the dependency graph and keeps future implementation review grounded in the traceability matrix.

## Final Acceptance Notes

The accepted plan is complete when section 01 and section 02 have both been implemented, reviewed, recorded, and verified with `npm test`. Section recording should update the traceability matrix rather than relying on manual status edits. If implementation discovers that the auth state contract differs from this plan, work should pause for a plan update instead of silently changing preference behavior.
