# Example Gallery

These scenario starters exist to exercise decomposition and domain-pack
selection. They are not full validated planning tracks; the validated tracks
remain `examples/saas` and `examples/typescript-app`.

| Scenario | Domain Packs |
|----------|--------------|
| `nextjs-saas` | frontend, auth, payments |
| `python-fastapi` | auth, infra |
| `rails-app` | frontend, data migration |
| `go-service` | infra, data migration |
| `data-migration` | data migration |
| `ai-agent-app` | AI products, auth |

Pass a scenario's `requirements.md` to `zagrosi-forge` using the
[command for your host](../../README.md#use). The same starters work in Codex
and Claude Code. Forge selects the appropriate workflow; use `zagrosi-project`
for decomposition alone. Request `standard` or `deep` when the scope needs more scrutiny.
