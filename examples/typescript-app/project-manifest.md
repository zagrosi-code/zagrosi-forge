<!-- FORGE_META
{
  "artifact_type": "project_manifest",
  "workflow": "zagrosi-project",
  "depth_mode": "lean",
  "source": "requirements.md"
}
END_FORGE_META -->

<!-- SPLIT_MANIFEST
01-auth
END_MANIFEST -->

# Project Manifest

One split: `01-auth`. Preference writes consume the session lookup created by auth.

| Split | REQ | Depends on | Owns boundary | Next command |
|---|---|---|---|---|
| 01-auth | REQ-001, REQ-002 | - | `01-auth/spec.md`, `src/auth`, `src/settings` | `$zagrosi-plan @examples/typescript-app/01-auth/spec.md` |

## Execution order

Create `section-01-auth-flow`, then dependent `section-02-preferences`, then run the full test command. No parallel production work; preference tests may draft against an agreed session interface. Keep shared types, middleware, validation, fixtures, secret-safe logging, rollback, and traceability consistent. The split blocks later account, billing, and dashboard work.
