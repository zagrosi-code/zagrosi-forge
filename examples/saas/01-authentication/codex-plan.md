<!-- FORGE_META
{
  "artifact_type": "implementation_plan",
  "workflow": "zagrosi-plan",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001"]
}
END_FORGE_META -->

# Implementation Plan

## Reader Note

This plan is self-contained for a fresh implementer. It covers the SaaS
authentication split only. The implementer should not need the original
conversation to understand the goal, current-state assumptions, file ownership,
TDD path, security constraints, rollout, rollback, or acceptance criteria.

## Current State Evidence

The example assumes a Python service with auth modules and pytest coverage.
Before implementation in a real repository, verify the actual layout with
`rg --files src tests`, then search for `oauth`, `session`, `auth`, `callback`,
`provider`, and `config`. The illustrative paths are `src/auth/config.py`,
`src/auth/oauth.py`, `src/auth/session.py`, and `tests/auth/test_oauth.py`.
If the repository uses a framework-specific route directory or a different test
tree, preserve the responsibilities while adapting names to local convention.

The project manifest establishes that authentication blocks billing and
dashboard splits. That makes this split a foundation. Future billing and
dashboard code should consume an authenticated user/session contract. They
should not need to understand provider callback payloads, provider config, or
state validation internals.

## Goal And Non-Goals

REQ-001 implements OAuth registration and sign-in. A valid OAuth callback
creates or resolves a local user and creates a session through the existing
session mechanism. Invalid state, provider denial, disabled providers,
configuration errors, and ambiguous accounts fail safely without creating
sessions.

Non-goals are billing, dashboards, teams, organization membership, account-link
UI, password reset, multi-factor auth, provider administration, durable profile
migration, and broad session framework replacement. If implementation exposes a
missing account-linking or identity-storage policy, stop and plan it separately
rather than expanding this section silently.

## Architecture

The design has three modules and one test file. `src/auth/config.py` validates
provider configuration. `src/auth/oauth.py` orchestrates callback handling.
`src/auth/session.py` creates the local authenticated session. `tests/auth/test_oauth.py`
proves the behavior through deterministic offline tests.

The callback flow is sequenced. First validate and consume state. Second map
provider denial or provider errors into safe local results. Third resolve
provider identity through an adapter boundary. Fourth apply local account
policy. Fifth delegate session creation to the session module. The callback
module should not know cookie details, signing keys, or session persistence
format.

## Architecture Rationale

This separation keeps auth behavior auditable. Provider configuration changes
are reviewed in one place. Callback security is reviewed in one place. Session
policy remains shared by OAuth, password login, and future auth methods. The
rejected alternative is to put everything in a route handler. That would make
it easy to log tokens, duplicate cookie creation, skip state validation in a
future route, or make billing/dashboard code depend on OAuth internals.

Another rejected alternative is to pull in a new auth framework just for this
split. A new framework may be appropriate for a real product, but it is outside
this example's scope unless the existing project already uses it. The goal is a
clear implementation boundary that Codex can follow inside an existing codebase.

## Contracts And Result Shapes

`src/auth/config.py` should expose a function equivalent to
`load_oauth_provider_config(provider_name: str) -> OAuthProviderConfigResult`.
The exact typing can be dataclasses, Pydantic models, TypedDicts, or existing
local patterns. The success branch includes provider name, client ID, callback
URL, enabled flag, and secret references. Failure branches include disabled
provider, unknown provider, and missing required fields.

`src/auth/oauth.py` should expose a function equivalent to
`handle_oauth_callback(payload, saved_state, dependencies) -> OAuthCallbackResult`.
Inputs include callback query values, saved state, provider name, correlation
ID, and dependencies for provider adapter, state consumption, account lookup or
creation, and logging. Result codes should include `success`, `invalid_state`,
`provider_denied`, `provider_error`, `provider_disabled`, `config_error`, and
`ambiguous_account`. Failure results must be safe to log.

`src/auth/session.py` should expose or reuse a function equivalent to
`create_session_for_user(user, context) -> SessionResult`. OAuth passes a local
user identity and context. Session code controls cookie shape, signing, expiry,
and storage. If the existing module already has this behavior, use it rather
than adding another wrapper.

## File Tree

```text
src/auth/config.py
src/auth/oauth.py
src/auth/session.py
tests/auth/test_oauth.py
```

## Phase Plan

Phase 1 writes tests and local fixtures. Add `tests/auth/test_oauth.py` with
valid callback, invalid state, provider denial, disabled config, missing config
fields, ambiguous account, and token logging tests. Run
`uv run pytest tests/auth/test_oauth.py` and confirm the failures are expected.

Phase 2 implements provider configuration validation in `src/auth/config.py`.
The first green path should make config tests pass without touching OAuth
callback logic. Config failures should be deterministic and safe.

Phase 3 implements callback orchestration in `src/auth/oauth.py`. Start with
state validation and provider denial. Then add provider identity resolution,
account policy, and session delegation. Keep provider calls behind testable
dependencies so tests never contact a real OAuth service.

Phase 4 integrates session creation in `src/auth/session.py` if no suitable
helper already exists. Preserve existing session compatibility. Do not change
anonymous or password-session behavior unless the tests prove the local module
requires a small shared helper.

Phase 5 runs the full verification command, updates traceability, and records
any repository-specific deviations from the illustrative paths.

## File Plan

`src/auth/config.py` gets the provider configuration contract. Required fields
are provider name, client ID, callback URL, enabled flag, and any reference to
provider secrets that the existing provider adapter needs. Missing required
fields should fail before a user starts a login attempt.

`src/auth/oauth.py` gets the callback result types, state validation path,
provider denial translation, provider adapter call, account lookup or creation,
ambiguous-account handling, safe logging, and session delegation. It should not
contain low-level cookie code.

`src/auth/session.py` either remains unchanged because it already has a session
creation helper, or it receives the smallest helper required to create a
session from a local user identity. If this helper is added, keep it generic so
future non-OAuth auth flows can reuse it.

`tests/auth/test_oauth.py` owns all REQ-001 tests. Use fake provider adapters,
fake state stores, fake user repositories, and log capture. Avoid network calls
and real secrets.

## TDD And Test Matrix

Write tests first. The minimum test names are
`test_valid_callback_creates_session`,
`test_invalid_state_rejects_callback`,
`test_provider_error_does_not_create_session`,
`test_disabled_provider_rejects_callback`,
`test_missing_provider_config_fails_validation`,
`test_ambiguous_account_does_not_silently_link`, and
`test_callback_does_not_log_tokens`.

Each test should tie back to REQ-001. The valid callback test fails until the
OAuth handler delegates to session creation. The invalid state test fails until
state validation happens before provider work. The provider error test fails
until denial and provider failures short-circuit. The config tests fail until
configuration validation is centralized. The ambiguous-account test fails until
the implementation refuses silent linking. The logging test fails until logs are
safe.

During implementation, run `uv run pytest tests/auth/test_oauth.py`. Before
completion, run `uv run pytest`. If the repository has lint, type checking, or
formatting commands, run those as discovered and record the result.

## Security And Privacy

The security-sensitive order is mandatory. Validate state first. Consume state
or otherwise prevent replay. Reject provider denial. Resolve provider identity
through a testable adapter. Apply account policy. Create a session only after
those gates succeed. Every failure path returns without creating a session.

Token privacy is part of the acceptance criteria. Logs may include provider
name, safe error code, and correlation ID. Logs must not include authorization
codes, access tokens, refresh tokens, cookies, or raw provider profile payloads.
Tests should use sentinel values and assert they do not appear in captured logs.

## Migration And Compatibility

No schema migration is required in the default example. The implementation
should use the existing local account model and session mechanism. If a real
repository lacks a durable external identity field, that is a migration
dependency and should become a separate plan. Do not hide a missing schema by
storing provider identity only in a session.

Backward compatibility means existing sessions continue to work and existing
non-OAuth auth routes are not broken. If a shared helper is added to
`src/auth/session.py`, it should be additive and covered by tests.

## Rollout

Ship the feature behind provider configuration. Deploy with providers disabled
or limited to non-production first. Enable one provider in a staging
environment, run automated tests, perform one manual callback check if the
environment supports it, then enable production configuration. Observability
should report callback rejected, callback accepted, account resolved, and
session created events with safe fields only.

## Rollback

Rollback is configuration-first. Disable the provider to stop new OAuth
callbacks while preserving existing sessions. If route wiring was added, back
out the route adapter separately from the pure auth modules if needed. If a
session helper was added and other auth flows use it successfully, do not
remove it during emergency rollback unless it is the source of the incident.

## Review Integration

Architecture review should confirm that provider config, callback orchestration,
and session creation remain separate. Security review should confirm state
validation order, replay behavior, account ambiguity, and token logging. Test
review should confirm the red tests fail for behavior rather than bad fixtures.
Product review should confirm the first provider and account-linking policy.

Accepted review edits should tighten these boundaries. Rejected review edits
should be documented if they would expand scope into billing, dashboards,
provider admin, or account-linking UI.

## Acceptance

REQ-001 is done when valid callbacks create sessions, invalid or replayed state
does not reach provider work, provider denial and provider errors do not create
sessions, disabled or incomplete provider configuration fails safely, ambiguous
accounts do not link silently, logs do not expose sentinel secrets, and
`uv run pytest` passes. Billing and dashboard sections may then depend on the
authenticated session contract without depending on OAuth internals.

## Detailed Current-State Review Checklist

Before editing code, inspect the existing auth package. Identify the module
that currently creates sessions, the module or route that currently checks
authenticated users, and the place where provider or environment configuration
is loaded. Record the discovered paths in section completion notes if they
differ from the example paths. Search results should answer these questions:
does the app already have an OAuth callback route, does it already store
external provider identity, does it already use signed cookies or server-side
sessions, and does it already centralize structured logging?

If the app has an existing user repository, use it. If it has an existing
transaction helper, use it for account creation or identity linking. If it has
no durable place for provider identity, stop before implementing successful
registration because the plan would otherwise create a session that cannot be
reliably reproduced on the next login. That is a schema and migration decision,
not a callback detail.

## Route Adapter Guidance

The plan prefers a pure callback function, but most SaaS applications also need
a route adapter. The route should parse query parameters, load saved state from
the current request context, call provider config validation, call the OAuth
callback service, and translate the structured result into a redirect or HTTP
response. It should not contain state comparison, account lookup rules, cookie
signing, or token logging decisions. This makes route tests small and keeps the
security behavior in unit tests.

If the framework requires response objects for setting cookies, the session
module can return a session instruction that the route applies, or the session
module can own a framework-aware adapter if that is the established local
pattern. Choose the option that best matches the repository. Do not split
session policy between route and OAuth code.

## Data Consistency And Idempotency

OAuth callbacks can be retried by browsers, users, test clients, and provider
redirects. State consumption is the idempotency boundary. Once state is
consumed, replay should not create another session or another account. If local
account creation and provider identity linking happen in separate operations,
wrap them in the repository's transaction pattern. A partial account without a
linked provider identity is a support problem and should be avoided.

For returning users, the callback should resolve the provider identity to the
same local user consistently. For first-time users, registration should either
create the user according to product policy or return a safe failure. For
duplicate email cases, prefer explicit ambiguity over silent linking. If the
product later wants account linking, that deserves its own plan, UI, and test
matrix.

## Error Handling And User-Facing Behavior

The service result should be safe and machine-readable. Route adapters can map
`invalid_state` to a safe login retry response, `provider_denied` to a normal
login canceled response, `config_error` to an operational failure, and
`ambiguous_account` to an account-support or explicit-linking path. Tests in
this split should assert result codes, not final UI copy. UI copy may change
without changing the security behavior.

Unexpected exceptions from provider adapters, repositories, or session helpers
should be caught at the OAuth boundary only if the repository convention is to
return structured operational failures. If local convention is to let a global
error handler capture unexpected failures, preserve that convention while still
ensuring no session is created and no secrets are logged.

## Observability And Alerting

Add structured events only where the repository already has logging or event
helpers. Useful event names are `oauth_config_rejected`,
`oauth_callback_rejected`, `oauth_account_resolved`, and
`oauth_session_created`. Fields should be limited to provider name, correlation
ID, local user ID when safe, and reason code. Do not log raw callback query
strings, authorization codes, tokens, cookies, or profile payloads.

Operationally, a spike in `config_error` means deployment or environment
configuration is broken. A spike in `invalid_state` may mean stale sessions,
CSRF attempts, or route mismatch. A spike in `ambiguous_account` means product
policy needs account-linking work. The plan does not build dashboards, but the
event taxonomy should make those signals possible.

## Documentation And Traceability Updates

After implementation, update `traceability.md` so REQ-001 points to
`codex-plan.md`, `codex-plan-tdd.md`, `section-01-oauth-foundation.md`, and
`tests/auth/test_oauth.py`. Update `decisions.md` if the repository uses
different paths, a different session helper, or a discovered account-linking
policy. Update `risk-register.md` if any residual risk remains, such as a
known missing route-level integration test or a provider adapter limitation.

Do not rewrite this plan to match the implementation after the fact. The plan
is the intent. Completion notes should record precise deviations and why they
were appropriate for the repository.

## Implementation Stop Lines

Stop and re-plan if any of these conditions appear. First, the repository has
no durable way to relate provider identity to local accounts. Second, existing
session creation is spread across multiple route handlers and there is no safe
shared helper to call. Third, the product owner expects automatic account
linking based only on provider email but no written policy exists. Fourth, the
provider adapter requires live network calls in unit tests. Fifth, the route
framework makes it impossible to test callback behavior without booting the
whole application.

These stop lines are not failures of implementation effort. They are signals
that the planning boundary has uncovered a larger architectural dependency.
The correct response is to create a migration, session consolidation,
account-linking, provider adapter, or route-test section. Continuing inside
this section would make the example look complete while hiding the riskiest
part of the real work.

## Completion Runbook

When the implementation is ready, run the targeted auth tests and the full test
suite. Read the failure output rather than treating any green result as enough.
Confirm that the tests exercise REQ-001 success and every failure path in the
TDD plan. Confirm that no sentinel secret appears in logs, captured exceptions,
or assertion output. Confirm that all session creation assertions go through
`src/auth/session.py` or the repository's equivalent session module.

Then update governance files. `decisions.md` should record any path adaptation
or account policy discovered. `risk-register.md` should record residual risk
such as missing route integration coverage. `traceability.md` should continue
to show REQ-001 coverage through the plan, TDD plan, section file, and tests.
Only after those updates should the section be marked complete for later
billing and dashboard work.
