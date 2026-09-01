# section-01-auth-flow

## Purpose and dependencies

Implement REQ-001. No prior dependency; it blocks `section-02-preferences`. Preserve password/anonymous sessions. No preferences, billing, teams, provider admin, profiles, or linking UI.

## Owned files

- `src/auth/callback.ts`
- `src/auth/session.ts`
- `src/auth/callback.test.ts`

## Tests first

Write red Vitest cases: `valid_callback_creates_session`, `invalid_state_rejects_callback`, `provider_denial_does_not_create_session`, `replayed_callback_is_rejected`, `ambiguous_account_returns_stop_line_result`, and secret-log safety using `secret-auth-code`/`secret-access-token`. Fixtures: `makeSavedState`, `makeCallbackParams`, `makeProviderIdentity`, `makeCallbackDeps`, `makeLoggerCapture`. After stubs, expected failure must be missing behavior, not imports.

## Implementation contract

Create pure orchestration:

```ts
completeOAuthCallback(input: OAuthCallbackInput, deps: OAuthCallbackDeps): Promise<OAuthCallbackResult>
```

Order: validate/consume state; return `invalid_state`; map `provider_denied`/`provider_error`; reject `replayed_callback`; resolve identity; return `ambiguous_account` absent linking policy; finally delegate session creation to `src/auth/session.ts` and return `success`. Never expose provider payloads, cookie details, codes, or tokens. Route/framework adapters stay thin; replay storage may remain an injected dependency.

## Risks, verification, rollback

Risks: invalid/denied/replayed callback creates session, silent linking, secret logs. Run `npm test -- src/auth/callback.test.ts`, `npm test`, then discovered `npm run typecheck`/`npm run lint`. Acceptance: valid callback creates one session; all failures create none; sentinels never appear. Rollback by disabling provider/callback routing or reverting its adapter while keeping existing sessions.
