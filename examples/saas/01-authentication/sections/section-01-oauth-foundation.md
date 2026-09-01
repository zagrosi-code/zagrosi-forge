# section-01-oauth-foundation

## Goal

Implement REQ-001 OAuth registration/sign-in; no billing, dashboard, linking UI, MFA, or schema work.

## Dependencies

None. Assumes durable provider identity and existing session contract; missing either is a stop line.

## Owned files

- `src/auth/config.py`
- `src/auth/oauth.py`
- `src/auth/session.py`
- `tests/auth/test_oauth.py`

## Tests first

Create REQ-001 red tests: `test_valid_callback_creates_session`, `test_invalid_state_rejects_callback`, `test_provider_error_does_not_create_session`, `test_disabled_provider_rejects_callback`, `test_missing_provider_config_fails_validation`, `test_ambiguous_account_does_not_silently_link`, `test_callback_does_not_log_tokens`. Expected failure: missing contract or forbidden downstream call. Test command: `uv run pytest tests/auth/test_oauth.py`.

## Implementation contract

Modify config for explicit enablement/required fields. Callback must consume state, reject denial, resolve identity/account transactionally, then call session owner once. Public result shape: `success|invalid_state|provider_denied|provider_error|provider_disabled|config_error|ambiguous_account`. Route stays thin. Log only provider/correlation/reason.

## Risks / rollback

Risks: replay, wrong-user link, partial writes, secret leakage, cookie drift. Failure paths create no session. Rollback: disable provider; retain users/sessions.

## Acceptance / verification

Done when targeted and `uv run pytest` pass; valid callback creates one existing-style session; invalid/replayed/config/ambiguous paths cause no forbidden work; sentinel secrets remain absent.
