# Traceability Matrix

| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |
|-------------|---------------|------------------|---------------|--------|
| REQ-001 | `codex-plan.md`, `codex-plan-tdd.md` | `section-01-auth-flow.md` | `src/auth/callback.test.ts` | Covered |
| REQ-002 | `codex-plan.md`, `codex-plan-tdd.md` | `section-02-preferences.md` | `src/settings/preferences.test.ts` | Covered |

## Coverage Notes

REQ-001 is carried from the source spec through the normalized spec, plan, TDD
plan, section index, auth-flow section, and callback tests. REQ-002 follows the
same path through the preferences section and preference tests. The repetition
is intentional because Forge outputs should make requirement coverage visible
without relying on memory of the original conversation.

## Expected Test Mapping

- REQ-001: `valid_callback_creates_session`,
  `invalid_state_rejects_callback`,
  `provider_denial_does_not_create_session`,
  `replayed_callback_is_rejected`, and token logging safety coverage.
- REQ-002: `unauthenticated_user_cannot_update`,
  `invalid_preference_payload_is_rejected`,
  `authenticated_user_updates_preferences`, and persistence failure coverage
  if the repository exposes that error path.
