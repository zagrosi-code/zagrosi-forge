# section-02-preferences

## Purpose and dependencies

Implement REQ-002. Depends on `section-01-auth-flow` and shared lookup in `src/auth/session.ts`; never import `src/auth/callback.ts`. Consume session lookup only. No second auth/cookie policy, profiles, avatars, notifications, billing, teams, dashboards, or new settings UI.

## Owned files

- `src/settings/preferences.ts`
- `src/settings/preferences.test.ts`

## Tests first

Write red `unauthenticated_user_cannot_update`, `invalid_preference_payload_is_rejected`, `authenticated_user_updates_preferences`, and `persistence_failure_returns_safe_error` when storage supports it. Fixtures: `makeSession`, `makeMissingSession`, `makeValidPreferencePayload`, `makeInvalidPreferencePayload`, `makePreferenceStore`. Expected failures after stubs: auth not checked, invalid value accepted, write absent, or unsafe error.

## Implementation contract

Create:

```ts
updateDisplayPreferences(session: SessionLookupResult, payload: PreferencePayload, deps: PreferenceDeps): Promise<PreferenceUpdateResult>
```

Authenticate first; return `unauthenticated` without validation/write. Then validate display-only values; return `validation_error`. Persist for the session user through existing storage; return normalized stored data as `success`, or safe `persistence_error`. Route and tests share this service. If no storage exists, stop for a persistence section; do not use memory.

## Risks, verification, rollback

Risks: auth bypass, cross-user write, pre-auth validation oracle, UI-copy assertions. Run `npm test -- src/settings/preferences.test.ts`, `npm test`, plus discovered lint/typecheck. Acceptance: missing sessions never write; invalid authenticated payloads fail; valid updates persist to the current user; storage failure is safe. Rollback by disabling route/UI or reverting this module; any schema change requires a separate migration/rollback plan.
