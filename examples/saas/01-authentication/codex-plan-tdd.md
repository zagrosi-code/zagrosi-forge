# TDD Plan

## Reader Note

This TDD plan is self-contained for REQ-001. It describes the tests to write
before implementing OAuth registration and sign-in for the SaaS authentication
split. The tests use pytest, local fixtures, and fake provider adapters. They
must not require live OAuth providers, real credentials, network access, or
production cookies.

## Requirement Coverage

REQ-001: Users must be able to register and sign in through OAuth, and a valid
OAuth callback must create a local authenticated session through the existing
session mechanism.

Every test in `tests/auth/test_oauth.py` should reference REQ-001 in its test
docstring, test name comments, or surrounding section notes. The purpose is to
make traceability obvious to future maintainers. A test that does not prove
valid callback success, safe callback failure, provider config validation,
account ambiguity handling, session creation, or token privacy does not belong
in this split.

## Test File And Command

Primary test file:

```text
tests/auth/test_oauth.py
```

Targeted command:

```text
uv run pytest tests/auth/test_oauth.py
```

Final command:

```text
uv run pytest
```

If the real repository uses a different command, preserve the behavioral test
matrix and record the actual command in implementation notes.

## Red Tests

`test_valid_callback_creates_session` covers the happy path for REQ-001. The
fixture includes enabled provider config, saved state, returned state, fake
authorization code, fake provider identity, local user identity, and a session
creation spy. Expected failure before implementation: the callback handler is
missing, returns no success result, or never calls session creation. Green
behavior: one session is created for the resolved local user and the result is
safe to pass to a route handler.

`test_invalid_state_rejects_callback` covers state validation for REQ-001. The
fixture changes only the returned state. Expected failure before implementation:
the callback accepts the invalid state or reaches provider identity lookup.
Green behavior: the result is `invalid_state`, provider lookup is not called,
account lookup is not called, and session creation is not called.

`test_provider_error_does_not_create_session` covers provider denial or provider
adapter failure for REQ-001. The fixture simulates a provider error such as
access denied or an adapter returning a safe error. Expected failure before
implementation: the callback has no provider-error branch or creates a session
despite the provider failure. Green behavior: the result is `provider_denied`
or `provider_error`, and no session is created.

`test_disabled_provider_rejects_callback` covers configuration gating. The
fixture returns a provider config object with `enabled` set to false. Expected
failure before implementation: disabled providers are treated like enabled
providers. Green behavior: the result is `provider_disabled`, and no provider
adapter call, account lookup, or session creation occurs.

`test_missing_provider_config_fails_validation` covers startup or setup safety.
The fixture omits a required client ID or callback URL. Expected failure before
implementation: missing fields are ignored until callback time or cause an
unclear exception. Green behavior: config validation returns a structured
config error before the callback succeeds.

`test_ambiguous_account_does_not_silently_link` covers account-linking safety.
The fixture returns a provider identity whose email or provider account maps to
an ambiguous local account state. Expected failure before implementation:
provider identity silently links to an account or creates a duplicate. Green
behavior: the result is `ambiguous_account`, and no session is created unless
the repository already has explicit linking policy.

`test_callback_does_not_log_tokens` covers privacy for REQ-001. The fixture
uses sentinel strings such as `secret-auth-code`, `secret-access-token`,
`secret-refresh-token`, and `secret-cookie-value`. Expected failure before
implementation: logs or error output include one of those sentinels. Green
behavior: captured logs include safe provider name, safe result code, and
correlation ID only.

## Fixture Matrix

Use local fixture factories so each test varies only the behavior it proves:

- `make_provider_config(enabled=True, missing=None)` returns provider config or
  config validation input.
- `make_callback_payload(state="valid-state", code="secret-auth-code")`
  returns provider callback values.
- `make_saved_state(value="valid-state", consumed=False)` returns saved state
  or state-store behavior.
- `make_provider_identity(email="user@example.com")` returns a safe fake
  identity.
- `make_user_repository(mode="existing" | "new" | "ambiguous")` returns local
  account behavior.
- `make_session_creator()` returns a spy that records calls.
- `make_log_capture()` returns captured structured log entries.

Fixtures should be deterministic. They should not import real provider SDKs or
read real environment secrets. If the application already has fixture patterns,
reuse those patterns while preserving these behavior knobs.

## Expected Failure Discipline

The first red run may fail because `src/auth/oauth.py` or the callback function
does not exist. After creating minimal stubs, failures should become
behavioral. Acceptable red failures are invalid state not rejected, provider
adapter called too early, session creation not called for success, session
creation called on failure, disabled provider accepted, ambiguous account
linked silently, or sentinel secrets appearing in logs.

Unacceptable failures are unclear fixture setup, missing pytest imports,
network calls, environment secret reads, or assertions tied to incidental error
copy. Fix those before writing production code. TDD is only useful here if the
red tests describe the product and security behavior.

## Green Implementation Sequence

Make config validation tests pass first in `src/auth/config.py`. Then make
invalid-state behavior pass in `src/auth/oauth.py`. Then make provider denial
behavior pass. Then add the success path with account resolution and session
creation. Finally add ambiguous-account and token-logging behavior. This order
keeps the riskiest failure modes visible and prevents the success path from
being built before the safety gates exist.

Avoid broad refactors during the green pass. If the repository needs a session
helper, add the smallest generic helper to `src/auth/session.py`. If the
repository needs durable identity storage, stop and create a migration plan
instead of extending this test file until it hides the missing data model.

## Refactor Pass

After the test matrix is green, refactor for clarity. Keep callback result
construction readable. Keep provider config validation centralized. Keep
session creation delegated. Keep log redaction close to the code that handles
provider payloads. Do not merge provider config, callback handling, and session
creation into one large route handler just because the tests pass.

## Final Verification

Run `uv run pytest tests/auth/test_oauth.py` during iteration and `uv run pytest`
before section completion. If lint, typing, or formatting commands exist in
the repository, run them too. Completion notes should record the commands, any
skipped command with reason, and whether each REQ-001 test passed.

## Review Of Test Adequacy

After the green pass, review the test suite as if it were the only artifact a
future maintainer reads. The names should communicate REQ-001 behavior without
opening the implementation. The fixtures should make clear which dependency is
being controlled: provider config, callback state, provider adapter, account
repository, session creation, or logging. If a test requires reading a large
fixture to understand the scenario, split the fixture or rename the builder.

The suite should include at least one assertion that no failure path calls the
session creator. This can be repeated in individual tests or captured through a
shared spy assertion helper. The suite should include at least one assertion
that provider work is skipped for invalid state. These two checks protect the
most important ordering constraints in REQ-001.

## When To Add Integration Tests

Add a route-level integration test only after the pure OAuth tests pass and the
repository has an obvious test pattern for framework routes. The integration
test should be small: given callback query parameters and saved state, the
route calls the service and applies the session result or safe failure. Do not
duplicate the entire provider matrix at the route level. The unit tests own the
security matrix; the integration test proves the route is wired to the same
contract.

If there is no route test harness, record that gap in completion notes rather
than inventing a new testing framework inside this section.
