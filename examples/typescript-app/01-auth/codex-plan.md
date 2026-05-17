<!-- FORGE_META
{
  "artifact_type": "implementation_plan",
  "workflow": "zagrosi-plan",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001", "REQ-002"]
}
END_FORGE_META -->

# Implementation Plan

## Reader Note

This plan is self-contained. A fresh implementer should not need the original
conversation to understand the intended behavior, file ownership, tests,
rollout, rollback, or risk boundaries.

## Current State Evidence

The current target shape is a TypeScript application with auth modules and
settings modules. Before implementation, verify the actual paths with `rg` and
`rg --files`: `src/auth/callback.ts`, `src/auth/session.ts`,
`src/settings/preferences.ts`, `src/auth/callback.test.ts`, and
`src/settings/preferences.test.ts`. If the repository uses a different route or
test layout, preserve the module responsibilities while following local naming.

## Goals And Non-Goals

REQ-001 adds an OAuth callback flow. REQ-002 adds account display preferences.
Billing, teams, and provider administration are non-goals.

## Architecture

Use `src/auth/callback.ts`, `src/auth/session.ts`, `src/settings/preferences.ts`,
and `src/settings/preferences.test.ts`. The callback validates provider state,
then session code stores a signed cookie. Preferences remain behind auth checks.

## Architecture Rationale

The callback module should own provider response parsing because provider error
mapping and state validation are OAuth-specific. The session module should own
cookie signing and lookup because that keeps password login, OAuth login, and
future auth mechanisms consistent. The rejected alternative is letting each
route handler create its own auth checks, which would make unauthenticated
settings writes and token logging harder to audit.

## Contracts

`src/auth/callback.ts` exposes a callback handler or pure function that accepts
provider callback payload, saved state, and provider configuration. It returns a
typed result for success, provider denial, invalid state, or replay. 
`src/auth/session.ts` exposes session creation and lookup helpers. 
`src/settings/preferences.ts` accepts a session lookup result plus a preference
payload and returns structured success or validation errors.

## File Tree

```text
src/auth/callback.ts
src/auth/session.ts
src/auth/callback.test.ts
src/settings/preferences.ts
src/settings/preferences.test.ts
```

## Phase Plan

Batch 1 writes callback and settings tests. Batch 2 implements callback state
validation and session creation. Batch 3 implements preference validation and
auth enforcement. Batch 4 runs the full test command and updates section
documentation.

## File Plan

Create `src/auth/callback.ts`, update `src/auth/session.ts`, create
`src/settings/preferences.ts`, and add `src/settings/preferences.test.ts`.

## Testing

Write Vitest cases first for valid callback, invalid state, unauthenticated
preference update, and valid preference update. Run `npm test`.

## Test Matrix

- `src/auth/callback.test.ts::valid_callback_creates_session`: fails until
  callback success delegates to session creation.
- `src/auth/callback.test.ts::invalid_state_rejects_callback`: fails until state
  validation happens before provider work.
- `src/auth/callback.test.ts::provider_denial_does_not_create_session`: fails
  until provider errors short-circuit safely.
- `src/settings/preferences.test.ts::unauthenticated_user_cannot_update`: fails
  until preferences require a session.
- `src/settings/preferences.test.ts::authenticated_user_updates_preferences`:
  fails until preference validation and persistence are wired.

## Security And Privacy

Validate OAuth state, protect tokens, avoid logging secrets, and enforce auth
permission checks before preference writes.

## Risks And Edge Cases

Handle provider denial, missing cookies, duplicate callback replay, invalid
preference payloads, and storage failures.

## Migration

No schema migration is required. Keep backward compatibility with existing
anonymous sessions.

## Rollout

Ship behind provider configuration and release with auth callback telemetry.

## Rollback

Disable provider configuration and revert preference write routing if errors
rise.

## Review Integration

The review focus is whether the plan keeps auth ownership single, prevents
token leakage, and rejects unauthenticated preference writes. Accepted review
edits should tighten those boundaries. Rejected edits should be recorded with a
rationale if they would broaden scope into billing, teams, or provider admin.

## Acceptance

REQ-001 and REQ-002 are complete when tests pass and invalid callback or
unauthenticated preference updates are rejected.

## Implementation Notes

The callback module owns provider response parsing, state comparison, provider
error mapping, and the transition into session creation. It should receive all
external values as explicit inputs so tests can construct valid and invalid
callback attempts without relying on global request state. Session code should
stay focused on signing, verifying, and clearing cookies; callback behavior
should call into the session helper instead of duplicating cookie details.

The preferences module owns validation and update behavior for authenticated
settings. It should accept a session object or session lookup result, reject
missing sessions before payload validation side effects, and return predictable
errors for invalid display preference values. Tests should cover the observable
contract rather than implementation internals so later storage changes can keep
the same behavior.

The implementation should avoid broad application rewrites. Keep provider
configuration reads in one place, pass typed payloads through the auth boundary,
and avoid adding a database migration unless a future section explicitly
requires durable user profile storage. Logging should include request IDs and
provider names but never token values, authorization codes, cookies, or raw
profile payloads.

## Current Behavior Assumptions To Verify

Before writing code, the implementer should verify whether the repository
already contains a route handler that receives OAuth callbacks. Search for
`callback`, `oauth`, `provider`, and `session` across `src/` and the test
directories. If a route already exists, it should become a thin adapter around
the contract described here. If no route exists, keep the first section focused
on the pure callback module and session helper; route wiring can be a small
addition only when local patterns make the route path obvious. This preserves
the plan's current state evidence while still allowing real repositories to
keep their file layout.

Also verify existing test tooling. This example assumes Vitest and `npm test`,
but a repository may use Jest, Node's built-in test runner, or a monorepo
workspace command. Preserve the test names and behavioral intent even if the
command changes. The planning artifact should record the final command used so
future implementers can reproduce the result.

## Detailed File-By-File Plan

`src/auth/callback.ts` should define the callback orchestration boundary. The
preferred public API is a function similar to
`completeOAuthCallback(input, dependencies)`, where `input` contains callback
query values, saved state, provider name, and correlation ID, and
`dependencies` contains provider identity lookup, state consumption, account
lookup, and session creation functions. Passing dependencies explicitly keeps
tests deterministic and prevents the callback module from reaching into global
request state. The return shape should be a discriminated union so route
handlers can map outcomes without parsing thrown errors.

`src/auth/session.ts` should expose the stable session boundary used by both
sections. Section 1 may add a function such as `createSessionForUser(userId,
context)` if no equivalent exists. Section 2 should use a lookup function such
as `getCurrentSession(request)` or the local equivalent. The module owns cookie
shape, signing, expiration, and clearing. OAuth and settings code should never
inline cookie details because that would create multiple auth policies.

`src/settings/preferences.ts` should expose a small service function, for
example `updateDisplayPreferences(sessionResult, payload, dependencies)`. It
should reject missing or expired sessions before payload validation, validate
allowed preference values for authenticated users, persist through the existing
settings or user repository boundary, and return a structured result. The
function should not import OAuth provider code and should not know how sessions
are created.

`src/auth/callback.test.ts` should own REQ-001. It should contain fixture
builders for valid callback payloads, invalid state values, provider-denied
payloads, replayed state, duplicate account ambiguity, and log-capture
sentinels. `src/settings/preferences.test.ts` should own REQ-002 and should
contain fixtures for authenticated sessions, missing sessions, valid
preferences, invalid preferences, and persistence failures.

## API And Result Contracts

Use explicit result codes even if the application later translates them into
HTTP responses. REQ-001 should support at least these callback statuses:
`success`, `invalid_state`, `provider_denied`, `provider_error`,
`replayed_callback`, and `ambiguous_account`. The success result carries only a
local user identity, provider name, and session result. The failure results
carry a safe reason and optional field metadata. They do not carry raw provider
responses.

REQ-002 should support `success`, `unauthenticated`, `validation_error`, and
`persistence_error`. `unauthenticated` must be produced before validation. That
ordering prevents unauthenticated callers from learning which preference values
are valid. `validation_error` can include field-level details after auth has
been proven. `persistence_error` should be safe for logs and user responses.

The plan does not require a specific TypeScript validation library. If the
repository already uses Zod, Valibot, Yup, or custom validators, reuse the
local convention. If no convention exists, a small hand-written validator for
the allowed display values is enough for this section.

## Logging, Secrets, And Privacy

Callback logging should include a correlation ID, provider name, and safe
result code. It should not include authorization codes, access tokens, refresh
tokens, cookies, raw query strings, or full provider profile payloads. Test
fixtures should include sentinel secret values and assertions that those values
do not appear in logs or thrown errors. This makes the privacy requirement
executable rather than rhetorical.

Preference logging should be quieter. A successful preference update may log
the user ID and changed keys if the repository already logs account actions.
Validation failures usually do not need logs. Persistence failures should log a
safe operational reason and correlation ID, not the full payload.

## Execution Order In Detail

Phase 1 writes the callback tests and minimal callback/session stubs. The
expected first red failure may be a missing module import. After the stub is
created, the red failure should become behavioral: state is not validated,
provider denial is not rejected, replay is not detected, or session creation is
not delegated. Do not proceed to green implementation until the test failure
proves the intended behavior.

Phase 2 implements callback behavior behind the public contract. Keep provider
work behind dependency functions so no test hits the network. State validation
and replay detection happen before identity mapping. Account ambiguity returns
a stop-line error unless an explicit local policy already exists.

Phase 3 writes preference tests and the preferences module. The first test
should prove unauthenticated requests cannot update. The second should prove
invalid payloads fail for authenticated users. The third should prove valid
authenticated payloads persist and return the stored result. Persistence is
mocked or faked through the repository's existing test pattern.

Phase 4 runs the full command, reviews logs and result shapes, updates
traceability, and records any skipped commands. A section is not complete until
the full configured command succeeds or an explicit environmental blocker is
documented.

## Review Checklist

Reviewers should confirm that REQ-001 appears in the spec, plan, TDD plan, and
`section-01-auth-flow.md`, and that REQ-002 appears in the same artifacts plus
`section-02-preferences.md`. They should confirm there is one session boundary,
not one for OAuth and another for preferences. They should inspect tests for
expected failure quality, fixture determinism, and absence of real credentials.

Security review should focus on state validation order, replay behavior,
provider denial handling, account ambiguity, unauthenticated preference writes,
and token logging. Architecture review should focus on module ownership and
whether route handlers stay thin. Test review should focus on behavioral
assertions rather than UI text snapshots.

## Rollback And Operational Notes

Rollback for REQ-001 is configuration-first. Disable the OAuth provider and
route callbacks away from the new flow while preserving existing sessions. If
the implementation added only local modules and tests, code rollback is a
normal revert. If route wiring was added, verify existing password or anonymous
session behavior still works after disabling OAuth.

Rollback for REQ-002 is to disable the preference update route or hide the
client path that calls it. Because this section should avoid schema changes,
rollback should not require data migration. If a real repository needs a
profile table or durable preference table, create a separate migration section
before writing data-changing code.

## Done Criteria

The work is done when `npm test` passes, REQ-001 valid callback creates a
session, REQ-001 invalid and denied callbacks do not create sessions, REQ-002
missing-session writes fail before validation, REQ-002 valid authenticated
updates persist, and no captured logs contain sentinel secrets. The completed
sections should include the exact commands run and any repository-specific path
changes from this example.

## Section Handoff: section-01-auth-flow

`section-01-auth-flow` should be enough for an implementer to start without
opening this plan. It must copy REQ-001, the callback result contract, the
session ownership rule, the concrete test names, the exact files in scope, and
the stop-line behavior for ambiguous accounts. The section should make clear
that callback state validation is first, provider denial is second, replay
rejection is third, account lookup is fourth, and session creation is last.

The section should also clarify what is allowed to vary. A repository may have
the callback code inside a framework route file, a server action, an API
handler, or a service module. That variation is acceptable if the provider
logic still calls a single session helper and the tests can exercise the
behavior without a network call. A repository may use a different test command,
but the section should keep the same test matrix and record the command that
was actually run.

Implementation should stay narrow. If the repository lacks account-linking
policy, return an `ambiguous_account` result rather than adding linking UI. If
the repository lacks durable replay storage, model replay through an injected
state-consumption dependency and leave durable storage for a future section. If
session creation requires route framework objects, put only the adapter in the
route layer and keep callback decisions in testable code.

## Section Handoff: section-02-preferences

`section-02-preferences` should copy REQ-002, the preference result contract,
the dependency on `src/auth/session.ts`, and the rule that missing sessions
fail before validation. The section should name the preference payload fields
as examples rather than immutable product decisions. If the target repository
already has allowed preference values, use those. If it does not, keep the
payload to low-risk display settings and avoid adding identity, billing, or
team behavior.

The preference section should be explicit about not importing provider code.
It consumes a session lookup result and calls a settings persistence helper.
This preserves the architectural boundary created in section 1. Tests should
prove persistence is not called for unauthenticated users, invalid payloads are
reported only after auth, valid payloads persist against the authenticated user
ID, and persistence failures return safe operational errors.

If the repository already stores preferences in a user profile table, this
section can use that table through existing repository helpers. If it has no
storage path, the implementer should stop and request a separate persistence
plan. Hiding preferences in memory would make tests pass while producing a
misleading example.

## Repository Adaptation Rules

The example paths are illustrative but the ownership boundaries are mandatory.
When adapting to a real codebase, map the files this way: callback orchestration
goes to the existing auth service or route-adjacent module; session creation
and lookup go to the existing session module; preference validation and writes
go to the existing account settings module; tests live beside the code or in
the repository's established test tree. Do not introduce a new directory style
just because the example uses `src/auth` and `src/settings`.

Before editing, run searches equivalent to `rg "session|oauth|callback|settings|preferences" src tests`
and `rg --files src tests`. Read package scripts to discover whether
`npm test`, `npm run test`, workspace commands, lint, and type-check scripts
exist. If local tooling differs, update the section completion notes. If the
repository has generated code or framework-specific route conventions, keep the
business logic testable outside those generated surfaces whenever possible.

## Failure Handling Detail

Callback failures should be deterministic and non-leaky. `invalid_state` means
the returned state does not match or cannot be consumed. `provider_denied`
means the provider explicitly returned a user denial or equivalent error.
`provider_error` means the provider adapter could not produce identity safely.
`replayed_callback` means the same state or callback was already consumed.
`ambiguous_account` means local account policy does not allow silent linking.
Every failure skips session creation.

Preference failures should also be deterministic. `unauthenticated` means no
valid session was supplied. `validation_error` means an authenticated caller
submitted unsupported display settings. `persistence_error` means storage
failed after auth and validation succeeded. Route handlers may translate these
to HTTP status codes or UI messages, but tests should assert the structured
results so product copy can change later.

## Documentation Updates

After implementation, update traceability and section completion notes rather
than rewriting the whole plan. The traceability matrix should show REQ-001
covered by the callback plan, TDD tests, and `section-01-auth-flow.md`; REQ-002
covered by the preference plan, TDD tests, and `section-02-preferences.md`.
Decision notes should record any repository-specific divergence, such as a
renamed route file, a different session helper name, or an explicit account
linking policy discovered during implementation.
