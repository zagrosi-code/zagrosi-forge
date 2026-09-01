<!-- FORGE_META
{
  "artifact_type": "implementation_plan",
  "workflow": "zagrosi-plan",
  "depth_mode": "lean",
  "requirement_ids": ["REQ-001", "REQ-002"]
}
END_FORGE_META -->

# Implementation Plan

## Goal, evidence, scope

Implement REQ-001 OAuth callback safety and REQ-002 authenticated display preferences. Current-state evidence assumes Vitest and these candidate files; verify with `rg`, `rg --files`, `rg --files src tests`, and `rg "session|oauth|callback|settings|preferences" src tests`:

```text
src/auth/callback.ts
src/auth/session.ts
src/auth/callback.test.ts
src/settings/preferences.ts
src/settings/preferences.test.ts
```

Follow local layout if different. Non-goals: billing, teams, provider admin, profiles, UI polish, broad rewrites, live-provider tests, or a new storage schema.

Runtime detection: inspect `package.json` scripts/dependencies and `tsconfig.json`. Test discovery: list existing tests and fixtures, then use the repository test command. Assumption ledger: Vitest and the listed layout are provisional; missing linking, replay, or storage policy is a stop line.

## Design contracts

Rationale: separating provider decisions, session persistence, and settings writes gives one reusable auth policy and isolates failures.

`completeOAuthCallback(input, dependencies)` in `src/auth/callback.ts` validates/consumes state, maps provider failures, resolves account ambiguity, then calls the single session boundary in `src/auth/session.ts`. Result codes: `success`, `invalid_state`, `provider_denied`, `provider_error`, `replayed_callback`, `ambiguous_account`. `getCurrentSession(request)` is the shared lookup.

`updateDisplayPreferences(sessionResult, payload, dependencies)` in `src/settings/preferences.ts` returns `success`, `unauthenticated`, `validation_error`, or `persistence_error`; auth precedes validation and persistence. Route handlers are adapters. Explicit dependencies keep tests offline. Logs exclude codes, tokens, cookies, raw queries/profiles; use correlation ID and safe status only.

## Tests-first sequence

1. `section-01-auth-flow`: write `src/auth/callback.test.ts::valid_callback_creates_session`, `src/auth/callback.test.ts::invalid_state_rejects_callback`, `src/auth/callback.test.ts::provider_denial_does_not_create_session`; also `replayed_callback_is_rejected`, ambiguity, and secret-log coverage. Expected failure: missing behavior after minimal stubs. Implement state -> denial/error -> replay -> account policy -> session creation.
2. `section-02-preferences` depends on section 1. Write `src/settings/preferences.test.ts::unauthenticated_user_cannot_update`, `src/settings/preferences.test.ts::authenticated_user_updates_preferences`, plus invalid payload and safe persistence failure. Expected failure: auth/validation/write behavior absent. Implement lookup -> validation -> storage.
3. Run targeted files, `npm test`, and discovered lint/typecheck. `npm run test` is an acceptable local alias. Record adapted paths and skipped commands.

## Risks, rollout, rollback

Security risks: invalid/replayed/denied callback creates a session; silent account linking; secrets in logs; settings bypass auth; payload validation leaks to anonymous callers. Missing linking/replay/storage policy stops work and creates a follow-up section. No migration is planned; preserve anonymous/existing sessions.

Roll out behind provider configuration with safe callback telemetry. Rollback REQ-001 by disabling provider/routing or reverting callback wiring while retaining sessions. Rollback REQ-002 by disabling preference route/UI or reverting its module; any discovered data migration needs a separate plan.

## Acceptance

Done when `npm test` passes; valid callbacks create exactly one signed session; all callback failures create none; missing auth fails before preference validation/write; valid preferences persist for the current user; captured logs contain no sentinel secrets. Record implementation drift in the section record instead of expanding this plan.
