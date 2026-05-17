# Bad Decisions Fixture

| Decision | Why |
|----------|-----|
| Inline auth logic in a route. | Faster. |

This fixture intentionally violates the Forge governance schema. The
`lint-artifact-schema` gate should require ID, Date, Decision, Alternatives,
Rationale, and Impact columns.
