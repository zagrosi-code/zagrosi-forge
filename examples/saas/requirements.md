# SaaS Platform Requirements

Build a small B2B SaaS platform that supports authenticated users, paid plan
access, and an operational dashboard. The project should be decomposed into
planning units that can be implemented safely with tests-first development.

## Product Context

The first release needs a trustworthy identity foundation before any money or
business data is handled. Authentication should establish a stable local user
and session contract. Billing should later depend on that authenticated user
contract rather than coupling itself to OAuth providers. The dashboard should
depend on both authentication and billing status so it can show account-specific
metrics without exposing data across users.

## Requirements

- Users can register and sign in through OAuth.
- Authenticated users have a durable local session the rest of the app can
  trust.
- Paid subscription state can be attached to authenticated accounts.
- The dashboard can show account-specific usage and billing status.
- Security, privacy, audit logging, provider configuration, and test fixtures
  should be treated as cross-cutting concerns across all splits.

## Constraints

Use the existing runtime, persistence, and test conventions of the target
repository. Do not introduce billing, dashboard, or team behavior inside the
auth split. Do not let later splits duplicate authentication logic. Each split
should produce clear specs, plans, TDD plans, implementation sections, and
traceability evidence.
