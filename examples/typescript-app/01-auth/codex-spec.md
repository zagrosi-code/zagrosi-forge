<!-- FORGE_META
{
  "artifact_type": "normalized_spec",
  "workflow": "zagrosi-plan",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001", "REQ-002"]
}
END_FORGE_META -->

# Normalized Spec

## Reader Note

This spec is self-contained for a fresh implementer. It describes the minimum
auth and account settings behavior needed before a section writer creates
implementation-ready sections.

## Current System Context

The planned TypeScript app has auth callback handling, session utilities, and
account settings code. The implementation must keep OAuth provider handling
separate from session persistence and must keep settings writes behind the same
auth boundary used by the callback flow.

REQ-001: Valid OAuth callbacks create a session after state validation.

REQ-002: Authenticated users can update account display preferences.

## Contracts

`src/auth/callback.ts` owns provider callback parsing and state validation.
`src/auth/session.ts` owns session signing, lookup, and auth state. 
`src/settings/preferences.ts` owns display preference validation and writes.
Tests live in `src/auth/callback.test.ts` and
`src/settings/preferences.test.ts`.

## Testing And Risks

Tests cover valid callback, invalid state, provider denial, unauthenticated
preference writes, invalid payloads, and successful updates. The main risks are
token leakage, duplicate callback replay, settings writes without auth, and
route handlers duplicating auth policy instead of calling the shared session
helper.

Implementation sections should preserve these boundaries even if concrete file
names differ in a real repository. Missing account-linking or replay policy is a
stop-line product question, not an invitation to invent silent behavior.

## Acceptance Criteria

REQ-001 is accepted when valid callbacks create sessions and invalid callbacks
do not. REQ-002 is accepted when authenticated users can update preferences and
unauthenticated requests are rejected.

## Detailed Requirement Narrative

The auth flow starts from a browser redirect back from an OAuth provider. The
application has previously generated a state value and stored enough state to
prove the callback belongs to the same browser session. The callback handler
receives query parameters from the provider, validates the state before any
provider work, maps provider-denied or provider-error callbacks into local
structured errors, and delegates all session creation to `src/auth/session.ts`.
The handler must not directly manipulate cookie details unless the existing
repository already centralizes cookie writing in that same module. The main
architectural promise is that callback handling decides whether the identity is
valid, while session handling decides how a valid local user becomes an
authenticated browser session.

The account preferences flow is deliberately smaller but depends on the same
auth boundary. A request to update display preferences must first produce an
authenticated session lookup result. If that lookup fails, the preference module
returns an unauthenticated result before validating or writing the payload. This
ordering matters because validation error details should not become an oracle
for unauthenticated callers. Only authenticated callers should receive
field-level validation results. Valid preference payloads are intentionally
small: display density, theme preference, or equivalent account-display options
that do not require a schema migration in this planning slice.

## Behavioral Contracts

`src/auth/callback.ts` exposes one clear entry point. A concrete repository may
name it `handleOAuthCallback`, `completeOAuthCallback`, or implement it as a
route handler, but the contract is the same: input is provider callback data,
provider config, and saved state; output is a discriminated result object. The
success branch includes a local user identity and enough data for
`src/auth/session.ts` to create a session. Failure branches include
`invalid_state`, `provider_denied`, `provider_error`, `replayed_callback`, and
`ambiguous_account`. The ambiguous account case is a stop-line condition unless
the repository already has an explicit account-linking policy.

`src/auth/session.ts` owns session persistence. It should expose a function that
creates a signed session from a local user identity and a function that looks up
the current session for settings writes. The exact cookie shape is an internal
detail of that module. Tests should assert observable behavior: a valid
callback results in a session, invalid callbacks do not, and preference writes
without a session fail.

`src/settings/preferences.ts` accepts a session lookup result and a preference
payload. It returns structured success or error results so tests can assert
behavior without depending on UI copy. The module should not import provider
code. It should depend only on session lookup and whatever persistence helper
the repository already uses for account settings.

## Non-Goals And Boundaries

This spec does not add billing, teams, organization membership, provider
administration, account linking UI, or durable profile tables. If the target
repository lacks a preferences storage mechanism, the implementer should either
use the smallest existing account settings storage path or stop and request a
follow-up plan. The spec also avoids adding a second auth framework. Existing
auth conventions win unless they contradict the security requirements above.

## Verification Summary

The finished implementation is verified with Vitest. Callback tests cover valid
callback, invalid state, provider denial, replayed callback, duplicate-account
ambiguity, and token logging safety. Preference tests cover missing session,
invalid payload, and successful authenticated update. The full acceptance
command is `npm test`. A targeted run against `src/auth/callback.test.ts` and
`src/settings/preferences.test.ts` is acceptable while iterating, but the final
section completion requires the configured full command.

## Data And Error Shapes

The exact names may change to match the repository, but the behavior should map
to explicit shapes rather than loose booleans. The OAuth callback result should
be a discriminated union with a `status` field. A success result carries the
local user ID, the provider name, and a session creation instruction. Failure
results carry safe machine-readable reasons such as `invalid_state`,
`provider_denied`, `provider_error`, `replayed_callback`, and
`ambiguous_account`. They must not carry raw authorization codes, access
tokens, refresh tokens, cookies, or full provider profile payloads.

Preference update results should follow the same pattern. A success result
carries the stored display preferences and the authenticated user ID. An
unauthenticated result must be returned before validating payload details. A
validation result may include field-level errors for authenticated callers, such
as unsupported theme or density values. Storage failures should be represented
as a distinct operational error so route handlers can log safely and return a
generic response without pretending the update succeeded.

## Implementation Constraints

Implementers should start by locating existing module and test conventions with
`rg --files src tests` and targeted searches for `session`, `auth`, `cookie`,
`preferences`, and `settings`. If the app already has an auth middleware or
session helper, reuse it. If there is already a settings persistence helper,
place preference writes behind that helper. The section writer may rename paths
to fit a real repository, but it should preserve the contract that provider
callback handling, session persistence, and account settings validation are
separate responsibilities.

No part of REQ-001 or REQ-002 should require a live OAuth provider, real
network calls, real secrets, or production cookies in tests. The examples use
fake provider payloads and fake session writers because the important behavior
is local decision-making. External provider SDK integration can be added later,
but this plan proves the application boundary first.

## Security And Privacy Detail

The stop-line security rule is simple: a session is created only after state has
been validated, provider denial has been ruled out, replay has been ruled out,
and account ambiguity has been resolved by existing policy. The callback must
never fall through to success on unknown states or unknown provider errors.
Preference writes must never happen for missing sessions. Logs may include a
request ID, provider name, and safe reason code. Logs must not include raw
callback query strings if those strings contain an authorization code.

Privacy also affects tests. Test fixtures should use sentinel strings such as
`secret-access-token` and then assert those strings do not appear in captured
logs or thrown error messages. This keeps the example grounded in behavior that
a real codebase can verify instead of relying on a prose promise.

## Open Questions And Stop Lines

The implementer should stop rather than invent behavior if the repository lacks
an account-linking policy, a session persistence boundary, or any storage path
for preferences. Account linking can create security and product surprises, so
the default behavior is to return `ambiguous_account` when provider identity
collides with an existing account. Preferences are scoped to display-only
settings; notification preferences, billing defaults, team roles, and profile
identity fields should be planned separately.
