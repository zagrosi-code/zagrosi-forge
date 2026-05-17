# Traceability Matrix

| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |
|-------------|---------------|------------------|---------------|--------|
| REQ-001 | `codex-plan.md`, `codex-plan-tdd.md` | `section-01-oauth-foundation.md` | `tests/auth/test_oauth.py` | Covered |

## Coverage Notes

REQ-001 appears in the normalized spec, implementation plan, TDD plan, section
index, implementation section, and test plan. The examples intentionally repeat
the requirement in every artifact so traceability tools can prove coverage and
future implementers can see the behavioral thread without reading the whole
package.

## Expected Test Mapping

- `test_valid_callback_creates_session`: proves successful registration or
  sign-in creates a session.
- `test_invalid_state_rejects_callback`: proves state validation happens before
  provider work.
- `test_provider_error_does_not_create_session`: proves provider denial is safe.
- `test_disabled_provider_rejects_callback`: proves config can roll out or back.
- `test_missing_provider_config_fails_validation`: proves missing config fails closed.
- `test_ambiguous_account_does_not_silently_link`: proves account safety.
- `test_callback_does_not_log_tokens`: proves privacy behavior.
