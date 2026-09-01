# SaaS Requirements

- REQ-001: OAuth registration/sign-in creates a durable local session.
- REQ-002: Authenticated accounts can hold paid subscription state.
- REQ-003: Account-scoped dashboard shows usage and billing status.

Order: identity -> billing -> dashboard. Reuse target runtime, persistence, and tests. Keep auth free of billing/dashboard behavior; later splits consume, never duplicate, auth. Security, privacy, audit logs, provider config, rollback, fake fixtures, and traceability cross-cut all splits.
