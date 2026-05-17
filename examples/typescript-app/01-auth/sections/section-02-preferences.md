# section-02-preferences

## Purpose

Implement REQ-002 for authenticated account display preferences.

## Tests First

Create `src/settings/preferences.test.ts` with failing Vitest tests for
unauthenticated rejection, invalid payload rejection, and successful update.

## Implementation

Create `src/settings/preferences.ts` and reuse `src/auth/session.ts` to require
an authenticated session before writes. Validate display preference payloads.

## Acceptance

REQ-002 is complete when `npm test` passes and unauthenticated requests cannot
update preferences. The implementation stays scoped to settings and session
lookup paths.

The section is self-contained with fixtures, security checks, and verification
steps. It depends on the session behavior from section-01-auth-flow.

## Notes

Use explicit fixtures for an authenticated session, a missing session, and an
invalid preference payload. Keep validation close to `src/settings/preferences.ts`
so route handlers can stay thin. The implementation should return structured
errors that tests can assert without depending on UI text. This section should
reuse the session helper created by section-01-auth-flow rather than adding a
second auth check path, which keeps REQ-002 tied to the same auth boundary.

## File Tree

```text
src/auth/session.ts
src/settings/preferences.ts
src/settings/preferences.test.ts
```

## Implementation Details

`src/settings/preferences.ts` should accept a session lookup result and a typed
preference payload. It validates the payload only after confirming the user is
authenticated, then returns structured success or validation errors. Route
handlers should call this module and avoid duplicating auth or validation rules.

## Risks

The main failure mode is creating a settings write path that bypasses the shared
session helper. The second risk is overfitting tests to UI copy rather than the
behavioral contract. Keep assertions on structured results and persistence.

## Dependencies And Non-Goals

This section depends on `section-01-auth-flow`. The dependency is the shared
session lookup contract in `src/auth/session.ts`, not the OAuth provider code.
Do not import `src/auth/callback.ts` into preference logic. Do not create a
second auth middleware, custom cookie parser, or route-local session rule. This
section does not implement profile identity, avatar uploads, notification
preferences, billing defaults, team settings, or dashboard personalization.

## Background Context

REQ-002 requires authenticated users to update account display preferences. The
security boundary is inherited from REQ-001: the application has one way to
look up the current session, and settings writes must use it. The preference
module should reject missing or expired sessions before validating payloads.
That order prevents unauthenticated callers from probing which values are
allowed. Once auth is established, the module can return field-level validation
errors for unsupported preference values.

The preference contract is intentionally small. Display settings can include
theme, density, language, or equivalent UI preferences already recognized by
the repository. Avoid adding database migrations unless the target app has no
existing account settings storage. If there is no storage path, stop and create
a separate persistence section rather than hiding data in memory.

## Public API Contract

Prefer a service function in `src/settings/preferences.ts`, with route handlers
adapting request objects into it. A representative signature is:

```ts
updateDisplayPreferences(session: SessionLookupResult, payload: PreferencePayload, deps: PreferenceDeps): Promise<PreferenceUpdateResult>
```

`SessionLookupResult` comes from `src/auth/session.ts`. `PreferencePayload`
contains display-only fields. `PreferenceDeps` contains the settings repository
or persistence helper plus optional safe logging. `PreferenceUpdateResult`
should include `success`, `unauthenticated`, `validation_error`, and
`persistence_error` branches. The success branch returns stored preferences,
not just the input payload, so tests can verify normalization.

## Detailed Tests First

Create `src/settings/preferences.test.ts` before production code. Use fixtures
such as `makeSession`, `makeMissingSession`, `makeValidPreferencePayload`,
`makeInvalidPreferencePayload`, and `makePreferenceStore`. The first red test,
`unauthenticated_user_cannot_update`, should pass a missing session and a valid
payload. It should assert that the result is `unauthenticated` and persistence
is not called. This test protects the most important security requirement.

The second test, `invalid_preference_payload_is_rejected`, should pass an
authenticated session and unsupported preference values. It should assert a
structured validation result with field details. The third test,
`authenticated_user_updates_preferences`, should pass an authenticated session
and valid payload, assert persistence receives the authenticated user ID, and
assert the result returns stored preferences. Add
`persistence_failure_returns_safe_error` if the repository's storage helper has
an established error pattern.

Run the targeted test file after writing the tests. After module stubs exist,
the expected failures should be behavioral: auth not checked, invalid payload
accepted, persistence not called, or result shape missing.

## Step-By-Step Implementation

First create `src/settings/preferences.ts` with types and a minimal safe
failure. Then wire the session lookup result into the function. If the session
is missing, expired, or otherwise unauthenticated, return `unauthenticated`
immediately. Do not parse or validate preference fields before this branch.

Next implement payload validation. Keep accepted values explicit and local to
the settings module unless the app already has a shared schema. Reject unknown
or unsupported values for authenticated callers. Normalize valid values if the
app convention requires it, for example lowercasing theme names or defaulting
omitted optional fields.

Finally call the existing preference persistence helper. If the helper returns
the stored row or account record, map it back into the success result. If it
throws or returns an operational failure, return `persistence_error` and log a
safe event with correlation ID and user ID only. Route handlers should translate
the result into HTTP or UI behavior without duplicating auth or validation.

## Verification And Acceptance

Run `npm test -- src/settings/preferences.test.ts` or the equivalent targeted
command while iterating. Before completion, run `npm test`, plus discovered
lint and type-check commands if present. REQ-002 is done when missing sessions
cannot update, invalid payloads fail for authenticated users, valid
authenticated payloads persist, persistence failures are safe, and no test
depends on UI copy.

## Rollback

Rollback by disabling the route or UI action that calls
`src/settings/preferences.ts`. Because this section should avoid schema changes,
rollback should not require data migration. If storage changes were unavoidable,
document the migration separately and do not mark this section complete until
the data rollback path is explicit.

## Implementation Notes For Real Repositories

If the repository stores settings through an ORM, API client, server action, or
profile service, use that existing boundary through a dependency argument or
local service call. Do not have route handlers mutate storage directly while
tests exercise a different service function. The route and the tests should
share the same preference logic. If the UI already has preference labels or
allowed values, reuse those constants so validation and presentation cannot
drift.

Keep the final implementation boring. A small validator, one persistence call,
structured results, and clear tests are enough for REQ-002. New settings pages,
optimistic UI, autosave, or account profile redesigns belong in later sections.
