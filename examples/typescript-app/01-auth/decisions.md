# Decision Log

| ID | Date | Decision | Alternatives | Rationale | Impact |
|----|------|----------|--------------|-----------|--------|
| DEC-001 | Example | Keep OAuth callback handling separate from session persistence. | Let route handlers set cookies directly. | One session boundary is easier to audit and reuse. | `src/auth/callback.ts` delegates to `src/auth/session.ts`. |
| DEC-002 | Example | Preference writes consume the shared session lookup. | Add a separate settings auth check. | REQ-002 should prove later features reuse auth rather than duplicating it. | `src/settings/preferences.ts` depends on session lookup, not provider code. |
| DEC-003 | Example | Tests assert structured results, not UI copy. | Snapshot route text or component copy. | Product text can change without changing auth behavior. | Tests remain stable across UI changes. |

## Notes

Real implementations should add decisions for actual provider choice, route
placement, validation library choice, replay storage, and supported preference
values.
