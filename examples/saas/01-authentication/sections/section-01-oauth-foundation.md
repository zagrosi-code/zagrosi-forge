# section-01-oauth-foundation

## Goal

Implement REQ-001 OAuth registration and sign-in for the SaaS authentication
split. A valid OAuth callback creates or resolves a local user and creates a
session through the existing session mechanism. Invalid state, provider denial,
disabled provider config, missing config fields, ambiguous accounts, and unsafe
provider errors fail without creating sessions.

## Dependencies And Non-Goals

This section has no prior section dependency inside the authentication split.
It blocks the later billing and dashboard splits because those features require
an authenticated user/session contract. It does not implement billing,
dashboards, team management, account-link UI, provider administration, password
reset, multi-factor auth, or durable profile migration. If the repository lacks
identity storage or account-link policy, stop and create a separate plan rather
than silently widening this section.

## Background Context

The architectural rationale is to keep provider configuration, callback
orchestration, and session creation separate. `src/auth/config.py` validates
whether a provider is enabled and complete. `src/auth/oauth.py` handles state
validation, provider denial, provider identity, account policy, safe logging,
and delegation to session creation. `src/auth/session.py` owns session shape,
signing, expiry, cookie behavior, and persistence. Route handlers should adapt
framework requests into this contract and translate structured results back
into HTTP responses.

This separation matters because the next SaaS splits will rely on a stable
session contract. Billing and dashboard code should never know how provider
callbacks are validated. They should ask whether a user is authenticated.

## File Tree

```text
src/auth/config.py
src/auth/oauth.py
src/auth/session.py
tests/auth/test_oauth.py
```

## Tests First

Create `tests/auth/test_oauth.py` before production code. Add these pytest
tests for REQ-001:

- `test_valid_callback_creates_session`
- `test_invalid_state_rejects_callback`
- `test_provider_error_does_not_create_session`
- `test_disabled_provider_rejects_callback`
- `test_missing_provider_config_fails_validation`
- `test_ambiguous_account_does_not_silently_link`
- `test_callback_does_not_log_tokens`

The valid callback test uses enabled config, matching state, fake provider
identity, a local account fixture, and a session creation spy. Expected failure
before implementation: callback handler missing, no success result, or session
creation not called. The invalid state test mutates only the returned state.
Expected failure before implementation: provider lookup or session creation is
called despite invalid state. Provider error tests simulate denial and adapter
failure. Config tests simulate disabled and incomplete providers. The ambiguous
account test simulates local account uncertainty. The logging test uses
sentinel values such as `secret-auth-code` and `secret-access-token`.

Run `uv run pytest tests/auth/test_oauth.py` after writing the tests. The first
failure may be an import failure. After stubs exist, failures should be
behavioral and tied to REQ-001.

## Implementation Details

Start in `src/auth/config.py`. Add or reuse a provider configuration validator.
It should return a structured success result for enabled complete providers and
structured failures for unknown providers, disabled providers, missing client
IDs, missing callback URLs, or missing secret references. Do not let missing
configuration produce unclear exceptions from deep inside callback handling.

Next implement `src/auth/oauth.py`. Prefer a public API equivalent to:

```python
def handle_oauth_callback(payload, saved_state, dependencies):
    ...
```

The dependencies object can be a dataclass, protocol, mapping, or local
service object depending on repository style. It should provide provider
identity lookup, state consumption, account lookup or creation, session
creation, and safe logging. The result should be a structured object or typed
dictionary with codes such as `success`, `invalid_state`, `provider_denied`,
`provider_error`, `provider_disabled`, `config_error`, and
`ambiguous_account`.

Validate state before provider work. If state is missing, mismatched, expired,
or already consumed, return `invalid_state` or the local equivalent and skip
provider lookup. Translate provider denial before account lookup. Resolve the
provider identity through a fakeable adapter. Apply local account policy. If
account linking is ambiguous and no explicit policy exists, return
`ambiguous_account` and do not create a session.

Finally call `src/auth/session.py` to create the session. If an appropriate
helper already exists, use it. If not, add the smallest generic helper that
creates a session from a local user identity and request context. Do not put
cookie signing, cookie names, expiry math, or session serialization inside
`src/auth/oauth.py`.

## Public API And Contracts

The callback result contract is the most important implementation artifact.
Route handlers and later splits can depend on it without learning provider
details. Success includes the local user identity and session result. Failure
includes only safe reason codes and safe metadata. No result includes raw
authorization code, access token, refresh token, cookie value, or full provider
profile payload.

The config contract should make provider enablement explicit. The session
contract should make session creation reusable by future auth flows. The tests
should assert these contracts through behavior, not by reaching into private
helpers.

## Security, Privacy, And Edge Cases

Stop-line risks are accepting invalid state, creating sessions after provider
denial, silently linking ambiguous accounts, and logging secrets. Edge cases
include missing callback state, duplicated state, expired state, disabled
provider, provider adapter timeout, duplicate email, and storage failure during
session creation. Every failure mode should produce a safe structured result
and no session.

Privacy rules apply to test output too. Captured logs, assertion messages, and
exceptions should not contain sentinel secrets. Store only the provider fields
needed by the existing account model.

## Verification

During implementation, run:

```text
uv run pytest tests/auth/test_oauth.py
```

Before completion, run:

```text
uv run pytest
```

If repository tooling includes lint, typing, or formatting commands, run them
and record the result. REQ-001 is done when the full test matrix passes, valid
callbacks create sessions, unsafe callbacks do not, and logs do not expose
sentinel secrets.

## Rollback

Rollback is configuration-first. Disable the provider to stop new OAuth
callbacks while preserving existing sessions. If route wiring was added, back
out the route adapter separately if needed. Avoid removing a generic session
helper if other auth paths now depend on it and tests show it is not the source
of the issue.

## Acceptance Criteria

This section is complete when `uv run pytest` passes, `tests/auth/test_oauth.py`
proves REQ-001 success and failure behavior, configuration failures are safe,
ambiguous accounts do not link silently, session creation is delegated to
`src/auth/session.py`, and token values are absent from logs and errors.

## Implementation Notes For Repository Variants

Framework-specific route files are allowed, but they should be adapters. Keep
REQ-001 decisions in the auth service so pytest can exercise them without a
live server. If the repository already has service classes, protocols,
repositories, or dataclasses, follow those conventions. If it uses plain
functions, keep the implementation plain. The section is prescriptive about
behavior and ownership, not about forcing a new Python style.
