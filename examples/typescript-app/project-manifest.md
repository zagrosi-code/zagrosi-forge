<!-- FORGE_META
{
  "artifact_type": "project_manifest",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "source": "requirements.md"
}
END_FORGE_META -->

<!-- SPLIT_MANIFEST
01-auth
END_MANIFEST -->

# Project Manifest

## Reader Note

This TypeScript example uses one split because the project brief is deliberately
focused: OAuth callback safety plus authenticated account display preferences.
The manifest is still explicit about dependencies, execution order,
parallelization, `$zagrosi-plan` usage, and cross-cutting concerns so it models
real Forge output rather than a tiny fixture.

## Overview

The split is `01-auth`. It includes REQ-001 OAuth callback handling and REQ-002
preference updates because preference writes depend directly on the session
lookup created by the auth flow. Keeping them together lets the plan define one
session boundary and then prove a second feature consumes it safely.

## Dependency Graph

| Split | Depends On | Blocks | Parallel |
|-------|------------|--------|----------|
| 01-auth | - | Later account, billing, or dashboard work | No |

## Execution Order

1. Run `$zagrosi-plan @examples/typescript-app/01-auth/spec.md`.
2. Let the plan create REQ-001 and REQ-002 sections.
3. Implement `section-01-auth-flow` before `section-02-preferences`.
4. Run the configured full test command after both sections.

## Parallelization Guidance

The split should not be implemented in parallel until the session interface is
agreed. `section-01-auth-flow` owns session creation. `section-02-preferences`
depends on session lookup. Parallel work may draft preference tests against an
agreed lookup shape, but production settings code should wait for the shared
session helper.

## Cross-Cutting Concerns

Shared TypeScript types, auth middleware, input validation, test fixtures,
provider secret handling, logging, rollback, and traceability should stay
consistent. Route handlers should be adapters over the same service contracts
used by tests.
