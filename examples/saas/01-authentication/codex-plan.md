<!-- FORGE_META
{"artifact_type":"implementation_plan","workflow":"zagrosi-plan","depth_mode":"lean","requirement_ids":["REQ-001"]}
END_FORGE_META -->

# Implementation Plan

## Goal / non-goals

REQ-001: safe OAuth registration/sign-in using existing local identity and session contracts. Out of scope: billing, dashboard, linking UI, MFA, provider admin, new auth framework, or hidden schema migration.

## Current-state evidence

Verify `pyproject.toml`, auth/session routes, identity storage, logging, and tests via `rg --files src tests` plus targeted search. Test discovery: locate existing auth fixtures and the repository test command. Planned ownership: `src/auth/config.py`, `src/auth/oauth.py`, `src/auth/session.py`, `tests/auth/test_oauth.py`. Assumption: durable provider identity and one session owner already exist; otherwise stop and plan migration/consolidation.

## Design contract

`config.py` validates explicit provider enablement and required fields. `oauth.py` orders: validate/consume state -> reject denial -> provider identity -> account policy/transaction -> session delegation. Safe result codes: `success`, `invalid_state`, `provider_denied`, `provider_error`, `provider_disabled`, `config_error`, `ambiguous_account`. `session.py` alone owns cookie/signing/expiry. Thin route parses input and maps results. Repeat callbacks fail after state consumption. Logs permit provider/correlation/reason only; never codes, tokens, cookies, profiles.

Rationale: one session owner prevents provider-specific policy drift.

## Tests first / verification

In `tests/auth/test_oauth.py`: `test_valid_callback_creates_session`, `test_invalid_state_rejects_callback`, `test_provider_error_does_not_create_session`, `test_disabled_provider_rejects_callback`, `test_missing_provider_config_fails_validation`, `test_ambiguous_account_does_not_silently_link`, `test_callback_does_not_log_tokens`. Expected red: missing contract or wrong side-effect order. Implement config -> negative callback paths -> success/session -> ambiguity/redaction. Run `uv run pytest tests/auth/test_oauth.py`, then `uv run pytest`.

## Risk, rollout, rollback

Main risks: CSRF/replay, takeover by email linking, partial account/link writes, secret leakage, session-policy drift. Use consumed state, explicit ambiguity, repository transaction, sentinel log assertions, one session owner. Roll out disabled, enable one provider in staging then production. Rollback: disable provider; preserve existing sessions/data.

## Review integration

Accepted: thin route, durable-identity stop line, state as idempotency boundary. Rejected: new framework or separate config section; both expand scope.

## Acceptance

Complete when valid callback creates exactly one existing-style session; all invalid paths perform no forbidden work; secrets stay absent; existing login and full pytest pass.
