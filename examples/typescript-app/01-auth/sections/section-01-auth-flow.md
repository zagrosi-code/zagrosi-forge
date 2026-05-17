# section-01-auth-flow

## Purpose

Implement REQ-001 for OAuth callback handling and signed session creation.

## Tests First

Create `src/auth/callback.test.ts` with failing Vitest tests for valid callback,
invalid state, provider denial, and replayed callback.

## Implementation

Create `src/auth/callback.ts` and update `src/auth/session.ts` to validate
state, reject provider errors, and set signed session cookies. Keep token values
out of logs.

## Acceptance

REQ-001 is complete when `npm test` passes and invalid callback state never
creates a session. The implementation is scoped to auth callback and session
files.

The section is self-contained with fixtures, auth edge cases, and verification
steps. The implementation should preserve existing anonymous behavior while
adding the callback path.

## Notes

Use test doubles for provider responses so the tests can represent valid
callbacks, denied authorization, and replay attempts without external network
calls. Keep token handling behind typed helpers and verify that token values are
not logged. Prefer small pure functions for state validation and provider error
translation, then wire those functions into the callback handler. This keeps the
red and green path narrow while still covering the security behavior that makes
REQ-001 valuable.

## File Tree

```text
src/auth/callback.ts
src/auth/session.ts
src/auth/callback.test.ts
```

## Implementation Details

`src/auth/callback.ts` should expose a callback function that accepts provider
payload, saved state, and provider configuration. It returns structured success,
invalid-state, provider-denied, or replay-rejected outcomes. `src/auth/session.ts`
owns session signing and cookie shape. The callback must call that helper rather
than duplicating cookie logic.

## Risks

The stop-line risks are accepting invalid state, creating a session on provider
denial, logging token values, or silently linking a duplicate account. If the
repository has no account-link policy, stop and ask before implementing linking.

## Dependencies And Non-Goals

This section has no prior section dependency. It creates the auth boundary that
`section-02-preferences` later consumes. It does not implement preference
updates, billing, team membership, provider administration, profile editing, or
account-linking UI. If the repository already has password login or anonymous
session behavior, preserve it. The new callback flow should add an OAuth entry
point without changing unrelated session semantics.

## Background Context

REQ-001 requires a valid OAuth callback to create a local authenticated
session. The architectural rationale is to separate provider callback handling
from session persistence. `src/auth/callback.ts` decides whether a provider
callback is valid and safe. `src/auth/session.ts` decides how a local user
identity becomes an authenticated browser session. Keeping those concerns apart
allows future password, magic-link, or SSO flows to share session policy instead
of each creating cookies differently.

The callback must validate state before provider work. It must reject provider
denial before user lookup. It must reject replayed callbacks before session
creation. It must return a structured ambiguity result if provider identity
collides with an existing account and the repository has no explicit policy.
These details are security behavior, not polish.

## Public API Contract

Prefer a pure orchestration function in `src/auth/callback.ts`, with route
handlers adapting request objects into the function. A representative signature
is:

```ts
completeOAuthCallback(input: OAuthCallbackInput, deps: OAuthCallbackDeps): Promise<OAuthCallbackResult>
```

`OAuthCallbackInput` should include provider name, callback parameters, saved
state, and a correlation ID. `OAuthCallbackDeps` should include state
comparison or consumption, provider identity lookup, account lookup or creation,
session creation, and safe logging. `OAuthCallbackResult` should include
`success`, `invalid_state`, `provider_denied`, `provider_error`,
`replayed_callback`, and `ambiguous_account` branches. The exact type names can
change, but the result must be explicit enough that tests do not inspect raw
exceptions.

## Detailed Tests First

Write `src/auth/callback.test.ts` before production code. Use fixture builders
such as `makeSavedState`, `makeCallbackParams`, `makeProviderIdentity`,
`makeCallbackDeps`, and `makeLoggerCapture`. The first test,
`valid_callback_creates_session`, should assert that a valid state and provider
identity call session creation exactly once and return a success result. The
second test, `invalid_state_rejects_callback`, should mutate only the returned
state and assert provider identity lookup and session creation are not called.

Add `provider_denial_does_not_create_session` with a provider-denied callback
fixture. Add `replayed_callback_is_rejected` with a consumed state fixture. Add
`ambiguous_account_returns_stop_line_result` if the account lookup fixture can
represent an existing local account with no linking policy. Add a log safety
test using sentinel values such as `secret-auth-code` and
`secret-access-token`, then assert the captured logs do not contain those
values.

Run the targeted callback test file after adding tests. The first failure may
be a missing module. After creating a stub, the failure should be behavioral:
session creation not called, invalid state accepted, replay ignored, or unsafe
result shape returned.

## Step-By-Step Implementation

First create or update `src/auth/callback.ts` with type definitions and a
minimal function body that returns a safe failure. This lets the tests compile
without hiding missing behavior. Next implement state validation. If the
repository already has signed state helpers, reuse them. If not, define a small
dependency interface so the section does not invent durable state storage.

Then implement provider denial and provider error translation. Provider-denied
input should return `provider_denied`. Unexpected provider adapter failures
should return `provider_error` with a safe reason. Do not log raw provider
payloads. After the failure paths are safe, implement successful provider
identity mapping and local user lookup. If account linking is ambiguous, return
`ambiguous_account` and stop.

Finally call `src/auth/session.ts` to create the session. If a suitable helper
already exists, use it. If not, add the smallest helper needed to create a
session from a local user identity and context. The callback module should not
know cookie names, signing keys, expiration math, or serialization details.

## Verification And Acceptance

Run `npm test -- src/auth/callback.test.ts` or the equivalent targeted command
while iterating. Before marking the section complete, run `npm test`. If the
repository has `npm run typecheck` or `npm run lint`, run those too. REQ-001 is
done when valid callbacks create sessions, invalid state never reaches provider
work, provider denial never creates a session, replay is rejected, ambiguous
accounts do not link silently, and sentinel secrets are absent from captured
logs.

## Rollback

Rollback is configuration-first. Disable the OAuth provider or route callbacks
away from this handler while keeping existing sessions intact. If route wiring
was added during implementation, revert that wiring separately from the pure
callback module so password or anonymous session behavior remains available.

## Implementation Notes For Real Repositories

If the repository uses Next.js, Remix, Express, Fastify, or another framework,
keep framework request parsing at the edge. Convert the request into the
callback input type, call the callback orchestration function, then translate
the structured result into the framework response. This keeps REQ-001 covered
by unit tests even when route integration needs a smaller smoke test. If the
repository already has dependency injection or service containers, follow that
pattern instead of inventing a new one for this section.
