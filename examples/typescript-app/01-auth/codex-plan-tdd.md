# TDD Plan

REQ-001: `src/auth/callback.test.ts::valid_callback_creates_session` fails
before callback implementation. REQ-001: `invalid_state_rejects_callback` fails
before state validation. These are expected failure tests before production
code exists.

REQ-002: `src/settings/preferences.test.ts::authenticated_user_updates_preferences`
passes only after preference writes enforce auth. Add `describe("preferences")`
coverage for unauthenticated rejection and invalid payloads. Run `npm test`.

## Callback Tests

- `valid_callback_creates_session`: fixture includes saved state, provider
  response, local user, and expected signed session. Expected failure before
  implementation: no session is created.
- `invalid_state_rejects_callback`: fixture mutates the saved state. Expected
  failure before implementation: callback accepts state or reaches provider
  work.
- `provider_denial_does_not_create_session`: fixture simulates provider denial.
  Expected failure before implementation: provider denial has no structured
  result.
- `replayed_callback_is_rejected`: fixture uses an already-consumed state value.
  Expected failure before implementation: replay is not detected.

## Preference Tests

- `unauthenticated_user_cannot_update`: missing session fixture. Expected
  failure before implementation: preference update does not check auth.
- `invalid_preference_payload_is_rejected`: invalid display option fixture.
  Expected failure before implementation: invalid values are accepted.
- `authenticated_user_updates_preferences`: authenticated session fixture.
  Expected failure before implementation: write path does not exist.

## Fixtures And Commands

Use small fixture factories for provider payloads, state values, session lookup
results, and preference payloads. Run `npm test` after the red tests are
written, then rerun targeted files while implementing each section.

## Red-Green Ownership By Section

Section `section-01-auth-flow` owns `src/auth/callback.test.ts`. The first red
test is `valid_callback_creates_session`. It constructs a saved state fixture,
a provider payload fixture, a provider config fixture, and a mock session writer.
Before production code exists, the test fails because there is no callback
entry point or because the entry point returns a missing-session result. The
green path proves the callback validates state, maps the provider identity to a
local identity fixture, and calls session creation exactly once.

The second red test is `invalid_state_rejects_callback`. It uses the same
provider payload but mutates the state value. Before implementation, the handler
either does not exist or cannot distinguish valid from invalid state. The green
path proves provider work and session creation are not called. The assertion
should inspect a structured `invalid_state` result and should also assert that
no token or authorization code appears in logs.

The third red test is `provider_denial_does_not_create_session`. Its fixture
models a provider callback containing an error code or denial flag. Before
implementation, there is no structured denial branch. The green path returns
`provider_denied`, skips session creation, and records a safe reason that does
not expose provider secrets. A fourth callback test, `replayed_callback_is_rejected`,
uses a state fixture that has already been consumed. If the existing repository
has no replay store, the section may model replay protection as a pure consumed
state dependency and leave durable storage to a future section.

Section `section-02-preferences` owns `src/settings/preferences.test.ts`. The
first red test is `unauthenticated_user_cannot_update`. It passes a missing
session lookup result and a valid payload. Before implementation, the preference
module either does not exist or validates payloads before auth. The green path
returns `unauthenticated` and does not call persistence. The second red test,
`invalid_preference_payload_is_rejected`, passes an authenticated session and an
invalid preference value. The green path returns field-level validation errors.
The third red test, `authenticated_user_updates_preferences`, passes an
authenticated session and valid payload and expects a persisted update.

## Fixture Matrix

Create fixture helpers instead of inline objects in every test. Recommended
helpers are `makeSavedState`, `makeProviderPayload`, `makeProviderDeniedPayload`,
`makeSession`, `makeMissingSession`, `makeValidPreferencePayload`, and
`makeInvalidPreferencePayload`. Fixtures should be deterministic and should not
hit the network. Provider access tokens, authorization codes, and cookies should
be fake sentinel strings so log-capture assertions can prove those sentinels are
not emitted.

## Expected Failure Discipline

After writing the tests for a section, run the targeted file and capture the
meaningful failure. A missing module failure is acceptable only for the first
test. Once the module stub exists, the expected failure should be behavioral:
invalid state not rejected, session creation not called, unauthenticated update
not blocked, or invalid payload not rejected. Do not write broad snapshot tests
for UI text. The behavior under test is the auth and settings contract.

## Final Verification

During section work, use targeted commands such as `npm test -- src/auth/callback.test.ts`
or the equivalent command supported by the repository. Before recording either
section complete, run `npm test`. If the repository has lint or type-check
commands in package scripts, run those as well and record the results in the
section completion notes.

## Test Data Ownership

The callback test file owns provider callback fixtures. Keep these fixtures
small and explicit. A valid callback fixture should include a provider name,
saved state value, returned state value, fake authorization code, fake provider
identity, local user identity, and expected session result. The invalid state
fixture should change only the returned state so the test proves the state
comparison is the deciding factor. The provider denial fixture should include a
safe provider error code without an authorization code. The replay fixture
should mark the saved state as already consumed through a dependency stub.

The preferences test file owns session and payload fixtures. A missing session
fixture should represent the exact shape returned by `src/auth/session.ts` when
lookup fails. A valid preference payload should use the smallest supported
display settings. An invalid payload should include unsupported values and
extra keys if the local validator is expected to reject unknown input. A
persistence failure fixture should simulate the repository helper returning or
throwing an operational error after auth and validation have succeeded.

## Red Path Quality Bar

The first run after adding tests should fail for meaningful reasons. Import
errors are acceptable only until the module stub exists. After that, expected
failures should identify missing behavior: callback accepts invalid state,
provider denial falls through, replay is ignored, session creation is not
called, unauthenticated preference writes reach persistence, invalid payloads
are accepted, or valid payloads cannot be saved. If a test fails because of a
bad fixture, unclear mock setup, or mismatched local test runner syntax, fix the
test before implementing production code.

Every red test should point to one requirement. REQ-001 tests live in
`src/auth/callback.test.ts`; REQ-002 tests live in
`src/settings/preferences.test.ts`. Shared fixture helpers are allowed, but
they should not hide the behavior under test. Prefer builders with explicit
overrides to large global fixture objects.

## Green Path Guardrails

Green implementation should be the smallest change that satisfies the tests
without weakening the architecture. For REQ-001, do not satisfy the success
test by directly setting a cookie in `src/auth/callback.ts`; the callback must
delegate to `src/auth/session.ts`. Do not satisfy provider denial by throwing a
raw provider error that leaks details; return a structured safe result. Do not
silently link duplicate accounts unless the repository already has that policy.

For REQ-002, do not satisfy preference tests by stubbing auth inside
`src/settings/preferences.ts`. The module should consume the shared session
lookup result. Do not validate payloads before checking auth. Do not assert on
UI copy or route-specific text because this example is about the application
contract, not presentation details.

## Refactor Pass

After the tests pass, refactor only inside the section scope. Extract pure
helpers for state comparison, provider error translation, preference payload
normalization, or result construction if those helpers make the tests clearer.
Avoid broad directory moves, new frameworks, or cross-cutting rewrites. If the
implementation reveals that account linking, storage, or route wiring is larger
than expected, stop and create another section instead of expanding this one
silently.

The final refactor pass should keep test names readable. A future reviewer
should be able to scan the test list and understand the behavior covered:
valid callback creates session, invalid state rejects callback, provider denial
does not create session, replayed callback is rejected, unauthenticated user
cannot update preferences, invalid preference payload is rejected, and
authenticated user updates preferences.
