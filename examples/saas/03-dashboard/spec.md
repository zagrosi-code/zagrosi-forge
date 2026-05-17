<!-- FORGE_META
{
  "artifact_type": "split_spec",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-003"]
}
END_FORGE_META -->

# Dashboard Spec

## Purpose

Implement REQ-003: authenticated users can view an account dashboard with
usage, billing status, and key operational summaries scoped to their account.

## In Scope

- Dashboard data loader that requires an authenticated session.
- Account-scoped usage summary and billing status display data.
- Empty, loading, permission-denied, and error states.
- Tests that prove users cannot see another account's data.
- Lightweight UI or API contract depending on the target repository.

## Out Of Scope

- Authentication implementation and billing provider integration.
- Data warehouse pipelines, advanced analytics, exports, notifications, and
  admin reporting.
- Redesigning global navigation or marketing pages.

## Dependency And Assumptions

This split depends on `01-authentication` and `02-billing`. It consumes the
authenticated user/session contract and billing status contract. It should not
read OAuth provider payloads or payment provider webhooks directly.

## Acceptance Criteria

REQ-003 is done when authenticated users can load their dashboard, unauthenticated
users are rejected, account scoping prevents cross-account data access, billing
status appears from the billing contract, and error states are covered by tests.

## Testing And Verification

Add tests for data loading, permission boundaries, empty state, billing status
mapping, and failure handling. Run targeted dashboard tests and the full
configured command. Include fixture data for at least two accounts to prove
isolation.

## Open Questions

- Which metrics appear in the first dashboard release?
- Is the dashboard rendered server-side, client-side, or as an API response?
- What account model is finalized by billing?
