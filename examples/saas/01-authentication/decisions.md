# Decision Log

| ID | Date | Decision | Alternatives | Rationale | Impact |
|----|------|----------|--------------|-----------|--------|
| DEC-001 | Example | Keep OAuth callback orchestration separate from session creation. | Put all logic in a route handler. | Separation keeps provider-specific behavior, account policy, and session signing auditable. | `src/auth/oauth.py` delegates session creation to `src/auth/session.py`. |
| DEC-002 | Example | Treat ambiguous account linking as a stop-line result. | Silently link accounts by provider email. | Email-only linking can create account takeover risk without explicit product policy. | Tests include `test_ambiguous_account_does_not_silently_link`. |
| DEC-003 | Example | Use provider configuration as rollout and rollback control. | Always enable provider when environment variables exist. | Explicit enablement prevents accidental production activation and gives a clean rollback path. | Disabled providers return safe structured errors. |

## Notes

These decisions are examples of the level of governance expected from a real
Forge plan. A target repository should add decisions for provider choice,
session helper naming, route placement, and any schema migration discovered
during implementation.
