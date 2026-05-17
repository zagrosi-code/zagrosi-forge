<!-- FORGE_META
{
  "artifact_type": "split_spec",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-002"]
}
END_FORGE_META -->

# Billing Spec

## Purpose

Implement REQ-002: authenticated users can subscribe to paid plans, and the
application can determine current billing status for protected SaaS features.

## In Scope

- Subscription plan model and account entitlement status.
- Payment provider checkout session creation through a provider adapter.
- Webhook verification and idempotent subscription state updates.
- Billing status lookup for authenticated accounts.
- Tests for checkout creation, webhook signature rejection, duplicate webhook
  handling, payment failure, cancellation, and entitlement reads.

## Out Of Scope

- Authentication implementation, OAuth callback handling, and session creation.
- Dashboard UI beyond exposing a billing status contract.
- Invoicing UI, tax calculation, coupons, enterprise contracts, and provider
  administration screens.

## Dependency And Assumptions

This split depends on `01-authentication`. Billing must consume the stable
authenticated user/session contract rather than duplicating auth checks. Payment
provider secrets must be loaded through the repository's configuration boundary
and excluded from logs.

## Acceptance Criteria

REQ-002 is done when authenticated accounts can start checkout, verified
webhooks update subscription state idempotently, invalid webhook signatures are
rejected, billing status can be read by later dashboard work, and rollback can
disable checkout without corrupting stored subscription state.

## Testing And Verification

Use provider adapter fakes and webhook fixtures. Run targeted billing tests
first, then the full configured test command. Include replayed webhook tests
and log redaction assertions for provider secrets.

## Open Questions

- Which payment provider is used first?
- What plan tiers exist for the initial release?
- Does the repository already have an account or organization billing model?
