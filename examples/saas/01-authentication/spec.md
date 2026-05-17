<!-- FORGE_META
{
  "artifact_type": "split_spec",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001"]
}
END_FORGE_META -->

# Authentication Spec

## Purpose

Implement REQ-001: users can register and sign in through OAuth, and valid
callbacks create local authenticated sessions that later SaaS features can
trust.

## In Scope

- OAuth callback validation and provider error handling.
- Provider configuration validation.
- Local account resolution or creation through existing policy.
- Session creation through the existing session mechanism.
- Tests for success, invalid state, provider denial, config errors, ambiguous
  accounts, and token logging safety.

## Out Of Scope

- Billing plans, payment provider integration, and entitlements.
- Dashboard analytics or reporting.
- Account-linking UI, provider administration, password reset, multi-factor
  auth, and team membership.
- Database migration unless the target repository has no external identity
  storage and a separate migration plan is approved.

## Acceptance Criteria

REQ-001 is done when a valid callback creates a session, invalid or replayed
state rejects before provider work, provider denial does not create a session,
disabled or incomplete config fails safely, ambiguous accounts do not silently
link, and logs do not expose provider tokens or authorization codes.

## Testing And Verification

Add pytest coverage in `tests/auth/test_oauth.py`. Use fake provider adapters,
fake state stores, fake account repositories, session creation spies, and log
capture. Run `uv run pytest tests/auth/test_oauth.py` during implementation and
`uv run pytest` before completion.

## Open Questions

- Which OAuth provider is enabled first?
- Does the target repository already store provider identity?
- What is the explicit account-linking policy for duplicate provider emails?
- Does route-level integration coverage already exist?
