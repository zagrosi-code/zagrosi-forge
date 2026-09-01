<!-- FORGE_META
{"artifact_type":"split_spec","workflow":"zagrosi-project","depth_mode":"lean","requirement_ids":["REQ-003"]}
END_FORGE_META -->

# Dashboard Spec

Dependencies: 01-authentication, 02-billing
Boundary: account-scoped dashboard query/presentation

## In scope

REQ-003: authenticated, account-scoped usage/billing summary plus empty, loading, denied, and error states. Test two-account isolation, unauthenticated denial, data loading, status mapping, failures.

## Out of scope

Auth or payment integration, warehouse analytics, exports, notifications, admin reports, global navigation redesign.

## Acceptance

Consumes session and billing-status contracts, never provider payloads/webhooks. Done when authenticated users see only their account, billing status maps correctly, unauthenticated/cross-account reads fail, and focused plus full tests pass.

## Open questions

Initial metrics, render/API form, final billing account model.
