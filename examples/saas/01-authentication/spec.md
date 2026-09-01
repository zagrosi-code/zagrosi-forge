<!-- FORGE_META
{"artifact_type":"split_spec","workflow":"zagrosi-project","depth_mode":"lean","requirement_ids":["REQ-001"]}
END_FORGE_META -->

# Authentication Spec

Dependencies: none
Boundary: OAuth callback/config/session delegation

## In scope

REQ-001: validate OAuth callback/config; resolve or create local account by existing policy; create existing-style session. Fail invalid/missing/expired/replayed state before provider work; fail denial, disabled/incomplete config, or ambiguous identity without session. Never log codes, tokens, cookies, or profiles.

## Out of scope

Billing, dashboard, teams, MFA, password reset, provider admin/linking UI, or unapproved schema migration.

## Tests and acceptance

Offline fakes in `tests/auth/test_oauth.py`; run `uv run pytest tests/auth/test_oauth.py`, then `uv run pytest`. Done when valid callbacks create sessions and every failure above is side-effect-free and redacted.

## Open questions

First provider; existing external-identity store; duplicate-email policy; route test harness.
