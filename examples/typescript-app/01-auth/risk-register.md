# Risk Register

| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |
|----|------|----------|------------|------------|---------|--------------|
| RISK-001 | Invalid OAuth state creates a session. | High | Medium | Validate state before provider work or session creation. | section-01-auth-flow | `invalid_state_rejects_callback`. |
| RISK-002 | Provider denial still creates a session. | High | Medium | Map denial to structured failure and assert session writer is not called. | section-01-auth-flow | `provider_denial_does_not_create_session`. |
| RISK-003 | Tokens or cookies appear in logs. | High | Medium | Use sentinel secrets and log capture assertions. | section-01-auth-flow | token logging safety test. |
| RISK-004 | Settings code bypasses shared auth. | High | Medium | Require session lookup result before validation or persistence. | section-02-preferences | `unauthenticated_user_cannot_update`. |
| RISK-005 | Tests overfit UI copy. | Medium | Medium | Assert structured service results and persistence calls. | section-02-preferences | preference test review. |

## Residual Risk

Replay protection may require durable state storage in a real app. If no
storage exists, model the dependency explicitly and create a follow-up section
for durable replay prevention.
