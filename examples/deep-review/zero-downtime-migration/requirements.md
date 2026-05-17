# Zero-Downtime Account Identity Migration

## Goal

Move a production SaaS application from email-only account identity to a durable
external identity model that supports OAuth providers, password sign-in,
organization membership, and account-link review without downtime.

## Current System

- Users currently sign in with email and password.
- Organization membership is keyed by `users.id`.
- Billing ownership, audit events, and invite acceptance all reference local
  users.
- The product team wants Google OAuth first, then GitHub OAuth.
- The system has a background worker, a relational database, and a feature flag
  service.

## Requirements

- REQ-001: Add an `external_identities` model without breaking existing
  password sign-in.
- REQ-002: Backfill identity rows for existing users with measurable progress,
  retry safety, and rollback.
- REQ-003: Allow OAuth sign-in only when provider identity maps to an existing
  local account or a reviewed linking policy.
- REQ-004: Preserve organization membership, billing ownership, audit history,
  and invite acceptance behavior through the migration.
- REQ-005: Provide operator dashboards or reports for ambiguous accounts,
  failed backfills, duplicate provider identities, and rollback readiness.

## Constraints

- No production downtime.
- No account takeover through email-only matching.
- No token, code, cookie, or profile payload leakage in logs.
- The migration must be implementable in bounded sections with tests first.
- Rollback must be defined for schema, writes, reads, and provider enablement.

## Review Pressure

The finished deep-mode plan should be reviewed from architecture,
security/privacy, migration/data, test strategy, product ambiguity, and
implementation feasibility perspectives. Accepted review findings must be
integrated into the plan and section files, while rejected or deferred findings
must retain rationale.
