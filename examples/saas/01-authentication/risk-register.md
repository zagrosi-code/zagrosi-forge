# Risk Register

| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |
|----|------|----------|------------|------------|---------|--------------|
| RISK-001 | Invalid or replayed state creates a session. | High | Medium | Validate and consume state before provider work or account lookup. | section-01-oauth-foundation | `test_invalid_state_rejects_callback` plus replay fixture if state store supports it. |
| RISK-002 | Provider denial or adapter failure still creates a session. | High | Medium | Map provider denial to safe result and assert session creator is not called. | section-01-oauth-foundation | `test_provider_error_does_not_create_session`. |
| RISK-003 | Duplicate provider email silently links to wrong account. | High | Medium | Return `ambiguous_account` unless explicit linking policy exists. | section-01-oauth-foundation | `test_ambiguous_account_does_not_silently_link`. |
| RISK-004 | Tokens, codes, cookies, or profile payloads appear in logs. | High | Medium | Use safe structured logging and sentinel secret assertions. | section-01-oauth-foundation | `test_callback_does_not_log_tokens`. |
| RISK-005 | Later billing and dashboard code duplicate auth logic. | Medium | Medium | Preserve session creation and lookup in `src/auth/session.py`. | section-01-oauth-foundation | Review file ownership and traceability before moving splits forward. |

## Residual Risk

Route-level integration coverage may vary by target repository. If no route
test harness exists, record that gap during implementation rather than adding a
new framework inside this section.
