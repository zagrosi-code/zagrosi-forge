# Research

## Current State Evidence

The fixture models a TypeScript application with Vitest. Runtime evidence names `package.json` as the runtime signal, auth and preferences modules as the implementation surface, and focused Vitest tests as the verification path.

The section plan splits the work into `section-01-auth-flow` and `section-02-preferences`. The auth section owns login/session behavior; the preferences section depends on the authenticated user state and verifies saved preference behavior.

The codebase shape assumed by this fixture is a client application with explicit auth state and a preferences layer that consumes the current user. `src/auth/session.ts` represents durable session state, `src/auth/login.ts` represents credential submission and login result handling, `src/preferences/store.ts` represents local preferences state, and `src/preferences/api.ts` represents persistence. The tests named in the TDD plan are Vitest tests that exercise these boundaries through user-facing state transitions rather than direct mutation of internal variables.

The runtime evidence is Node-oriented. `package.json` provides the primary command surface, and the fixture uses `npm test` as the configured verification command. TypeScript and Vite configuration files are expected runtime signals, but the implementation plan avoids framework-specific claims that are not needed for auth and preferences. The important contract is stable auth state, not a particular UI library.

The two-section split is intentional. Auth flow establishes the identity contract: login succeeds or fails, session state persists, logout clears state, and consumers can tell whether a user is authenticated. Preferences then uses that contract to load and save settings for the authenticated user. Reversing the order would force preferences tests to invent user identity and would weaken the integration evidence.

The plan assumes tests can use deterministic storage or API fixtures. Auth tests should not require a real backend. Preferences tests should use a controlled persistence boundary that can represent success, unauthenticated failure, validation failure, and network or storage failure. That is enough to verify the section contracts without depending on external services.

The existing Forge examples suite treats this directory as a benchmark, so planning artifacts must be complete enough to survive independent review. Research, evidence, interview notes, plan, TDD plan, review integration, sections, governance tables, and traceability all need to remain aligned around REQ-001 and REQ-002.

## Risks

The main risks are auth state drift between the route and store layers, preferences being saved without an authenticated user, and tests passing against mocked state that does not match runtime behavior. The plan mitigates this by keeping ownership explicit and requiring tests for both auth flow and preferences persistence.

The auth-flow section should verify the user-facing login path, session persistence, invalid credential handling, and logout state cleanup. It should avoid duplicating session policy in UI components. Components should consume the auth state through the existing store or context contract instead of deriving separate local truth.

The preferences section should start only after the auth-flow behavior is stable. Preferences persistence depends on a known authenticated user identity, so tests should cover loading preferences for the current user, saving updates, rejecting unauthenticated writes, and preserving preferences across reloads. Fixtures should avoid global mutable auth state that could make tests order-dependent.

The implementation should keep API contracts explicit. Preference payloads need deterministic validation, predictable error shapes, and stable handling for network or storage failures. The test command remains `npm test`, and focused Vitest coverage should be run before any broader verification.

## Auth Flow Research

Auth flow needs a single source of truth. If login state is represented in route-local state, component-local state, and a shared store at the same time, later preference behavior can observe a different user than the login flow produced. The plan therefore prefers a shared session contract that components consume rather than re-deriving authentication status in each feature.

The login result contract should make failure explicit. A valid login should produce a user/session shape; invalid credentials should produce a stable error result; network or API errors should produce a distinct retryable failure; logout should clear both in-memory and persisted session state. Tests should assert these outcomes as state transitions, not only as text rendered in a component.

Session persistence is a compatibility point. If the app already restores user state from storage, the implementation should preserve that behavior. If no persistence exists, the section should define the minimal persistence expected by the preferences section. The preferences implementation must not depend on hidden globals or test-only state because that would make persistence behavior brittle.

## Preferences Research

Preferences are user-scoped data. Saving preferences without an authenticated user is a correctness and privacy issue because it can attach settings to the wrong identity or to anonymous storage that later appears under a user. The preferences section must reject unauthenticated writes and should test the rejection through the same auth state contract established in section 01.

Preference payloads should be validated before persistence. The fixture can use simple settings, but the plan should still require deterministic behavior for invalid values, missing fields, and persistence failures. Tests should assert both that valid preferences are saved and that invalid payloads leave existing preferences unchanged.

Loading preferences needs clear defaults. If a user has no saved preferences, the UI or store should expose documented defaults. If loading fails, the error shape should be stable enough for the caller to render or retry. The plan does not need to prescribe detailed UI copy, but it should prevent silent failure or partial state mutation.

## Alternatives Considered

One alternative is merging auth and preferences into a single implementation section. That is rejected because it hides the dependency and makes the implementation harder to review. Another alternative is implementing preferences first with a mocked user. That is rejected because the integration risk is exactly whether preferences use the real authenticated user contract.

Another alternative is testing only UI text. That can miss persistence and state-contract bugs. The preferred strategy uses Vitest tests that verify login/session state and preference persistence behavior through the public store or API boundary the app actually uses.

## Traceability Notes

REQ-001 maps to auth-flow behavior: login, invalid credentials, session restore, logout cleanup, and a stable authenticated user state. REQ-002 maps to preferences behavior: default loading, current-user persistence, validation, unauthenticated rejection, and persistence failure handling. The research keeps those requirements separate because they have different implementation owners and different failure modes.

The traceability matrix should reference both section files and the focused Vitest tests. It should not rely only on the broad implementation plan. If a later implementer reads just `sections/section-02-preferences.md`, they should understand exactly which preference files and tests belong to REQ-002 and why auth-flow completion is a dependency.

This example also serves as a benchmark for Forge planning quality. A complete planning directory needs enough research, review, integration notes, TDD design, governance, and traceability for another agent to resume without the original conversation. That is why this research file records both implementation assumptions and rejected shortcuts.

## Verification Notes

Focused implementation can run a single Vitest file while a section is red or green, but final verification should use `npm test`. A future implementation should also inspect the diff with `patch-scope` and `implementation-drift` so preferences changes do not spill into unrelated routes, billing, profile management, or UI polish.

Auth-flow test data should be deterministic: valid credentials, invalid credentials, restored persisted session, and logout. Preferences test data should include existing saved preferences, empty preferences that load defaults, invalid preference payloads, unauthenticated writes, and persistence failures. These fixtures are enough to catch the important integration defects without adding a real backend dependency.

The planning record intentionally avoids prescribing component styling or routing text. Those are product details outside the requirement. The implementation contract is the state and persistence behavior, and the tests should verify that contract directly.

## Resume Notes

If this plan is resumed later, the implementer should start with `section-01-auth-flow` and verify that the auth state contract is explicit before opening preferences work. The relevant paths are `src/auth/session.ts`, `src/auth/login.ts`, and `tests/auth-flow.test.ts`. The expected red tests should fail because login/session behavior is absent or incomplete, not because test setup cannot import modules.

Only after auth-flow verification passes should the implementer open `section-02-preferences`. The relevant paths are `src/preferences/store.ts`, `src/preferences/api.ts`, and `tests/preferences.test.ts`. Tests should prove that preferences use the current authenticated user, reject missing auth state, and preserve stable defaults when no saved preferences exist.

The final implementation record should update traceability from planned to implemented for each requirement only after the matching section is recorded. That prevents a later reader from assuming preferences were finished just because auth-flow work passed.

This preserves Forge's section-by-section implementation contract.

## Commands

- `npm test`
- `python3 scripts/zagrosi_skills.py forge-score --planning-dir examples/typescript-app/01-auth --depth standard --strict`
