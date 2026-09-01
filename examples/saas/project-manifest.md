<!-- FORGE_META
{"artifact_type":"project_manifest","workflow":"zagrosi-project","depth_mode":"lean","source":"requirements.md"}
END_FORGE_META -->

<!-- SPLIT_MANIFEST
01-authentication
02-billing
03-dashboard
END_MANIFEST -->

# Project Manifest

Capability/risk splits prevent one plan hiding auth, payment, or isolation failures.

| Split | REQ | Depends on | Owns boundary | Next command |
|---|---|---|---|---|
| 01-authentication | REQ-001 | - | OAuth callback/config/session delegation | `$zagrosi-plan @examples/saas/01-authentication/spec.md` |
| 02-billing | REQ-002 | 01-authentication | billing/provider state | `$zagrosi-plan @examples/saas/02-billing/spec.md` |
| 03-dashboard | REQ-003 | 01-authentication, 02-billing | account-scoped dashboard query/presentation | `$zagrosi-plan @examples/saas/03-dashboard/spec.md` |

## Execution order

1. `$zagrosi-plan @examples/saas/01-authentication/spec.md`
2. `$zagrosi-plan @examples/saas/02-billing/spec.md`
3. `$zagrosi-plan @examples/saas/03-dashboard/spec.md`

Shared cross-cutting contract: local user/session identity, config, audit/log redaction, fake fixtures, rollback, traceability. Provider secrets and personal data never enter logs.

Parallel research/planning is safe; implementation follows dependency sequence above.
