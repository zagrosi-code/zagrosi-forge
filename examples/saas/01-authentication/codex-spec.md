<!-- FORGE_META
{
  "artifact_type": "normalized_spec",
  "workflow": "zagrosi-plan",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001"]
}
END_FORGE_META -->

# Normalized Spec

## Reader Note

This normalized spec is self-contained for a fresh implementer. It describes
the authentication split for the SaaS example after project decomposition and
before implementation planning. Billing and dashboard work depend on this
split, but they are not implemented here.

## Current System Context

The SaaS example models a Python service with an existing auth package shape:
`src/auth/config.py`, `src/auth/oauth.py`, `src/auth/session.py`, and
`tests/auth/test_oauth.py`. The concrete repository may have different names,
but the ownership boundary is stable. Provider configuration belongs in config
code. OAuth callback validation belongs in OAuth code. Session creation belongs
in the session module. Tests belong near the auth package and must not contact
real providers.

REQ-001: Users must be able to register and sign in through OAuth, and a valid
OAuth callback must create a local authenticated session through the existing
session mechanism.

This requirement covers both first-time registration and returning sign-in
because both flows share the same callback safety contract. A provider identity
is accepted only after callback state is validated, provider denial has been
ruled out, configuration is enabled and complete, and local account policy has
resolved whether the provider identity maps to an existing user or a new user.

## Functional Requirements

- REQ-001: A valid OAuth callback creates or resolves a local account according
  to existing account policy, then creates a session through `src/auth/session.py`.
- REQ-001: Invalid state, missing state, expired state, or replayed state fails
  before provider identity lookup and before session creation.
- REQ-001: Provider denial and provider errors return safe local errors and do
  not create a session.
- REQ-001: Provider configuration is validated before callback handling can
  succeed. Missing client ID, missing callback URL, disabled provider, or
  unknown provider name fails deterministically.
- REQ-001: Duplicate email or ambiguous account-linking cases do not silently
  link accounts unless the repository already has explicit account-linking
  policy.
- REQ-001: Logs and errors may include provider name, correlation ID, and safe
  reason codes, but must not include raw authorization codes, access tokens,
  refresh tokens, cookies, or complete provider profile payloads.

## Non-Goals And Boundaries

This split does not implement billing, dashboard analytics, subscription
entitlements, teams, organization invitations, provider administration screens,
account-linking UI, password reset, magic links, multi-factor auth, or durable
profile migration. It may create the smallest local account record needed by
the existing auth model, but it should not introduce a broad user-management
redesign. If a real repository lacks an external identity field or account-link
policy, stop and plan that migration separately.

The split should not add a second session framework. Existing session policy
wins. OAuth code delegates to the session module rather than setting cookies or
tokens directly.

## Contracts

`src/auth/config.py` owns provider configuration validation. It should expose a
loader or validator that returns an enabled provider configuration with provider
name, client ID, callback URL, and any secret references required by the
existing provider adapter. Missing or disabled config returns a safe
configuration error before login proceeds.

`src/auth/oauth.py` owns callback orchestration. It accepts callback payload,
saved state, provider name, and dependencies for provider identity lookup,
account lookup or creation, state consumption, and safe logging. It returns a
structured result instead of leaking raw provider errors. Representative result
codes are `success`, `invalid_state`, `provider_denied`, `provider_error`,
`provider_disabled`, `config_error`, and `ambiguous_account`.

`src/auth/session.py` owns session creation. OAuth passes it a local user
identity and request context. Session code owns cookie shape, signing, expiry,
and persistence. Route handlers should translate the OAuth result into HTTP or
framework responses without duplicating auth logic.

`tests/auth/test_oauth.py` owns REQ-001 verification. It should use local
provider adapter fixtures and fake state values. The tests should be able to
run offline with `uv run pytest tests/auth/test_oauth.py`.

## Security And Privacy

State validation is the primary security gate. The callback must not call a
provider adapter, create a user, or create a session until state has been
validated and consumed. Provider denial must short-circuit. Ambiguous identity
must stop rather than silently link accounts. Logs must use safe structured
fields. Test fixtures should include sentinel secret strings and assert those
strings are not present in captured logs or error output.

Privacy matters even in the example. Provider profile payloads can contain
email, name, avatar URL, locale, and provider-specific identifiers. Store only
the fields required by the existing account model. Avoid writing raw provider
payloads to application logs or persistence tables.

## Acceptance Criteria

REQ-001 is accepted when a valid OAuth callback creates a session through the
existing session mechanism, invalid state rejects before provider work,
provider denial does not create a session, disabled or incomplete provider
configuration fails safely, ambiguous accounts do not link silently, and
`uv run pytest tests/auth/test_oauth.py` plus the full configured test command
pass. The next SaaS splits may depend on a stable authenticated user/session
contract but should not need to know OAuth callback details.

## Verification Summary

The minimum test matrix covers valid callback, invalid state, provider denial,
disabled provider configuration, missing provider configuration fields,
ambiguous account, and token logging safety. If the repository has integration
tests for route wiring, add a small route-level test after the pure OAuth tests
pass. The final verification command for the example is `uv run pytest`.

## Data Ownership And Account Policy

The local account model remains the source of truth for application identity.
Provider identity is only an authentication input. A provider may return email,
display name, avatar, provider account ID, locale, or other profile fields, but
REQ-001 should persist only what the existing account model and external
identity policy require. If the repository already has a table or document for
external identities, use it through existing data-access code. If it does not,
do not invent a partial migration inside the callback function.

For first-time registration, the provider identity can create a local account
only when existing product rules allow it. For returning sign-in, provider
identity should resolve to the same local account every time. If provider email
matches an existing local account but provider identity is not linked, the safe
default is `ambiguous_account`. This avoids account takeover through provider
email assumptions.

## Operational Requirements

The callback flow should be observable without exposing private data. Safe
events include config rejected, callback rejected, account resolved, and session
created. Each event may include provider name, correlation ID, and safe reason
code. The events should make it possible to distinguish user denial from system
configuration failure, because those require different operational responses.

Configuration should support disabled providers. A disabled provider is a
normal rollout and rollback state, not an exceptional crash. The application
should fail closed when required configuration is missing. A provider should
not become enabled because an environment variable happens to exist if the
explicit enablement flag is false.

## Repository Adaptation Notes

The example names Python files because they are easy to read, but a real
repository may use Flask, FastAPI, Django, Litestar, or another framework. Keep
framework adapters thin. Request parsing can live in a route, but provider
config validation, callback decisions, account policy, and session creation
should remain testable outside route framework objects. If existing code uses
dependency injection, service classes, or repository objects, follow that local
style rather than forcing the example signatures literally.

The final implementation should leave a stable contract for later splits:
given a request, the app can determine the authenticated user/session without
the caller knowing OAuth provider details. That is the product value of this
authentication foundation.
