<!-- FORGE_META
{
  "artifact_type": "split_spec",
  "workflow": "zagrosi-project",
  "depth_mode": "lean",
  "requirement_ids": ["REQ-001", "REQ-002"]
}
END_FORGE_META -->

# Auth And Preferences Spec

Dependencies: none
Boundary: `01-auth/spec.md`, `src/auth`, `src/settings`

## In scope, dependencies, and boundary

REQ-001: parse OAuth callbacks; validate state; reject provider denial, replay, and ambiguous linking; create sessions only through the shared session module. REQ-002 depends on REQ-001's session output: authenticate through shared lookup before validating or writing display preferences.

Tests: `src/auth/callback.test.ts`, `src/settings/preferences.test.ts`; fake providers, sessions, and stores; structured results, not UI copy. Logs exclude tokens, codes, cookies, and raw provider payloads. `npm test` must pass.

## Out of scope

Out of scope: billing, teams, organizations, provider admin, linking UI, profiles, notifications, dashboards, a second auth framework, or route-local cookie policy.

## Acceptance criteria and open questions

Done when REQ-001 valid callbacks create sessions, every callback failure creates none, REQ-002 authenticated updates persist, and unauthenticated/invalid updates do not. Open: first provider, replay helper, supported preference values, linking policy. Missing policy is a stop line; do not invent it.
