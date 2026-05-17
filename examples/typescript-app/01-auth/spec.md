<!-- FORGE_META
{
  "artifact_type": "split_spec",
  "workflow": "zagrosi-project",
  "depth_mode": "standard",
  "requirement_ids": ["REQ-001", "REQ-002"]
}
END_FORGE_META -->

# Auth And Preferences Spec

## Purpose

Implement REQ-001 OAuth callback handling and REQ-002 authenticated account
display preferences for a TypeScript application.

## In Scope

- OAuth callback parsing, state validation, provider denial handling, replay
  handling, and session creation through the shared session module.
- Preference validation and authenticated preference writes through the shared
  session lookup.
- Vitest coverage for callback success, callback failure, unauthenticated
  preference writes, invalid preference payloads, and successful updates.
- Safe logging that excludes tokens, authorization codes, cookies, and raw
  provider payloads.

## Out Of Scope

- Billing, teams, organization membership, provider admin, account-linking UI,
  profile redesign, notification settings, and dashboard behavior.
- Adding a second auth framework or duplicating cookie/session policy in route
  handlers.

## Acceptance Criteria

REQ-001 is done when valid callbacks create sessions, invalid state and
provider denial do not, replay is rejected, and ambiguous accounts do not link
silently. REQ-002 is done when unauthenticated users cannot update preferences,
invalid payloads fail for authenticated users, and valid authenticated payloads
persist. The full `npm test` command must pass.

## Testing And Verification

Add tests in `src/auth/callback.test.ts` and
`src/settings/preferences.test.ts`. Use fake provider payloads, fake session
helpers, and fake preference stores. The tests should assert structured results
rather than UI copy.

## Open Questions

- What provider is enabled first?
- Does the repository already have a replay store or state-consumption helper?
- What preference values are supported in the product UI?
- Does account-linking policy exist, or should ambiguity return a stop-line
  result?
