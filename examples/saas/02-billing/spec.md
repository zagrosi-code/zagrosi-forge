<!-- FORGE_META
{"artifact_type":"split_spec","workflow":"zagrosi-project","depth_mode":"lean","requirement_ids":["REQ-002"]}
END_FORGE_META -->

# Billing Spec

Dependencies: 01-authentication
Boundary: billing/provider state

## In scope

REQ-002: authenticated account plan/entitlement, provider-adapter checkout, verified idempotent webhooks, status read contract. Test checkout, bad signatures, replay, failure, cancellation, entitlement, redaction.

## Out of scope

Auth/OAuth/session implementation, dashboard UI, taxes, coupons, invoices, enterprise contracts, provider admin.

## Acceptance

Outputs status while consuming the authentication user/session contract. Done when checkout starts, verified webhooks update once, invalid signatures fail, status is readable, and disabling checkout preserves stored state. Use fake provider/webhook fixtures; run focused then full configured tests.

## Open questions

Provider, initial tiers, account/organization billing model.
