# Workflow Contract

Project manifests and split specs should carry stable IDs.

Add a `FORGE_META` block near the top:

```markdown
<!-- FORGE_META
{
  "artifact_type": "project_manifest",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "source": "requirements.md"
}
END_FORGE_META -->
```

Use requirement IDs in split specs:

- `REQ-001`
- `REQ-002`

Each split spec should preserve the IDs it owns so `$zagrosi-forge:zagrosi-plan` can trace them
through plan, section, and test coverage.
