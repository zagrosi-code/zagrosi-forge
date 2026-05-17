<!-- FORGE_META
{
  "artifact_type": "project_manifest",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "source": "requirements.md"
}
END_FORGE_META -->

<!-- SPLIT_MANIFEST
01-authentication
02-billing
03-dashboard
END_MANIFEST -->

# Project Manifest

## Reader Note

This manifest is the output shape expected from `$zagrosi-project` before
running `$zagrosi-plan` on each split. It is intentionally more detailed than a
directory list. A fresh implementer can see why the project was split this way,
which dependency comes first, which work can run in parallel, and which
cross-cutting concerns must stay consistent.

## Overview

The SaaS platform is split by capability and risk boundary: authentication,
billing, and dashboard. Authentication comes first because every later feature
needs a stable authenticated user/session contract. Billing comes second
because plan state and payment events belong to authenticated accounts. The
dashboard comes third because it consumes identity and billing state while
adding user-facing reporting behavior.

## Dependency Graph

| Split | Depends On | Blocks | Parallel |
|-------|------------|--------|----------|
| 01-authentication | - | 02-billing, 03-dashboard | No |
| 02-billing | 01-authentication | 03-dashboard | Yes after auth |
| 03-dashboard | 01-authentication, 02-billing | - | No until billing contract exists |

## Execution Order

1. Run `$zagrosi-plan @examples/saas/01-authentication/spec.md`.
2. Run `$zagrosi-plan @examples/saas/02-billing/spec.md` after auth exposes a
   stable session/user contract.
3. Run `$zagrosi-plan @examples/saas/03-dashboard/spec.md` after dashboard
   inputs from auth and billing are explicit.

## Split Rationale

Authentication is security-sensitive and foundational, so it should not be
implemented at the same time as billing or dashboard UI. Billing has external
provider state, payment failure modes, webhook security, and subscription
entitlements. Dashboard work is mostly product experience and data access, but
it inherits the auth and billing contracts. Keeping these as separate planning
units avoids one giant plan that hides security and operational risks.

## Parallelization Guidance

Billing and dashboard research can happen while authentication is implemented,
but production billing or dashboard code should wait for the auth contract.
After authentication is complete, billing implementation can proceed while
dashboard designers or planners prepare read-only dashboard requirements. The
dashboard implementation should not begin until billing exposes plan status and
usage data shapes.

## Cross-Cutting Concerns

Security, audit logging, environment configuration, test fixtures, user/session
identity, rollback strategy, and traceability are shared concerns. Each split
should document how it consumes or extends those concerns rather than
duplicating them. Provider secrets, payment secrets, and user data must never
appear in logs or test fixtures except as fake sentinel values used for
redaction assertions.
