# Research

## Current State Evidence

The fixture models a Python SaaS authentication slice. Planning evidence names `pyproject.toml` as the runtime signal, `src/auth/oauth.py`, `src/auth/session.py`, and `src/auth/config.py` as implementation ownership, and `tests/auth/test_oauth.py` as the focused pytest surface.

The relevant Forge helper behavior is exercised through `uv run pytest`, `lint-plan`, `lint-sections`, `traceability`, `lint-implementation-readiness`, and `forge-score`. The examples suite validates this planning directory through `examples/evals/suite.json`.

The codebase pattern assumed by this fixture is a small service-oriented auth boundary rather than a route-heavy implementation. The OAuth callback should be interpreted by an auth service module, then delegated to session creation once provider identity, state, and local account policy have all been checked. That keeps provider-specific behavior out of cookie policy and lets existing password login remain the reference behavior for local session creation.

The fixture intentionally names concrete files even though the example is not a runnable SaaS product. `src/auth/oauth.py` represents callback orchestration, state validation, provider denial handling, and mapping provider identities into local auth results. `src/auth/session.py` represents session creation, cookie attributes, and existing password-login compatibility. `src/auth/config.py` represents provider configuration validation and startup failure behavior. `tests/auth/test_oauth.py` represents the focused regression surface that would fail before implementation.

The most important existing behavior to preserve is that session creation has one owner. Duplicating session creation in a callback route would make valid OAuth callback tests pass while slowly diverging from password-login cookie policy. The plan therefore treats session creation as a dependency rather than a new implementation site. OAuth-specific code should return a validated identity and policy result; the session module should turn an accepted local user into a session.

Runtime evidence is intentionally Python-centered. The repository root contains `pyproject.toml` and `uv.lock`, so the plan uses `uv run pytest` as the primary verification command and `python -m pytest` as a candidate fallback. The Forge release checks also compile `scripts/zagrosi_skills.py`, validate plugin JSON, run example snapshot checks, and validate project manifests. Those checks are not substitutes for application OAuth tests, but they are enough evidence for the fixture's planning workflow.

The plan should not rely on live OAuth providers, browser redirects, or external secrets. Provider behavior can be represented with deterministic fixtures: successful provider identity, provider denial, malformed callback payload, duplicate local email, missing client configuration, and invalid state. That keeps tests repeatable and avoids leaking external provider assumptions into the implementation plan.

## Risks

The main implementation risks are invalid OAuth state creating sessions, provider denial leaking into session creation, ambiguous account linking, and token or secret exposure in logs. The plan keeps session ownership in `src/auth/session.py` and provider callback handling in `src/auth/oauth.py`.

The section should preserve the existing password-login session behavior. OAuth callback code must validate state before touching provider identity data, must represent provider denial as a controlled failure, and must avoid logging provider tokens or raw callback payloads. Duplicate-account ambiguity is treated as a stop-line condition because automatic linking can attach an external identity to the wrong local user.

The testing strategy should focus on deterministic unit coverage rather than live provider calls. Fixtures should model signed state, tampered state, provider denial, duplicate email results, missing provider configuration, and successful callback identity mapping. Test assertions should verify both the positive session-creation path and the negative guarantee that invalid callbacks do not create sessions.

The implementation boundary is intentionally narrow. `src/auth/oauth.py` owns provider callback interpretation and validation. `src/auth/session.py` remains the single owner of local session creation and cookie policy. `src/auth/config.py` owns provider configuration validation so startup failures are deterministic and do not appear only at callback time.

## Security And Privacy Research

The security-sensitive part of this work is not the existence of an OAuth callback; it is the ordering of callback checks. State validation must run before provider identity is trusted, before a local account is looked up, and before session creation is attempted. If state validation happens late, a forged callback can exercise account matching or logging paths that should never run. The section therefore needs explicit tests proving invalid state short-circuits before a session is created.

Provider denial should be represented as an expected negative result, not an exception path that accidentally falls through into session logic. A denied callback must return a controlled failure shape and must not create or mutate local sessions. The same rule applies to missing configuration: startup or setup validation should report missing client ID, client secret, callback URL, or provider metadata before runtime callback handling depends on those fields.

Token handling is a privacy boundary. Tests should assert that access tokens, refresh tokens, ID token contents, provider payloads, and raw callback query strings are not emitted to logs. The plan does not need a full logging implementation, but it should require log capture in tests for the failure paths most likely to include sensitive data: invalid state, provider denial, duplicate account ambiguity, and missing config.

Duplicate-account ambiguity is a product and security decision. If a provider returns an email that already exists locally, automatic linking may be convenient but unsafe without a verified ownership policy. This plan treats ambiguity as a stop-line behavior. The callback returns a failure requiring explicit account-link policy rather than silently attaching the provider identity to the existing user.

## Testing Research

The smallest red/green path is a failing pytest file that starts with `test_valid_callback_creates_session`. The initial failure should be meaningful: the callback API either does not exist, does not validate state, or cannot delegate to the session module. Once that test passes, negative tests should lock down invalid state, provider denial, duplicate email, missing config, and token logging.

Fixtures should separate provider payloads from local user records. Provider fixtures can include provider user ID, email, display name, state, and token fields. Local user fixtures can include existing user ID, verified email status, and session creation policy. This separation prevents tests from assuming that every provider identity maps directly to a local session.

State validation fixtures should include a signed valid state, a tampered state, an expired state, and a state value missing from local storage. The section does not need a production-grade crypto implementation in the plan, but it should describe the contract clearly enough for implementation to preserve the state-before-session ordering.

The acceptance test command remains `uv run pytest`. A focused command such as `uv run pytest tests/auth/test_oauth.py` is useful while implementing the section, but the final verification should include the repository's configured test command so existing password-login behavior remains protected.

## Alternatives Considered

One alternative is putting the entire OAuth callback flow into a route handler. That is rejected because it duplicates session policy and makes it harder to test invalid state before side effects. Another alternative is mocking session creation completely and asserting only that the callback returns success. That is rejected because the requirement is session creation, not a provider identity result in isolation.

Another alternative is accepting duplicate email linking during the first implementation pass and tightening it later. That is rejected because ambiguous linking is one of the highest-risk behaviors. The fixture is intentionally small enough that a safe failure for ambiguous accounts can be planned and tested in the first section.

The preferred design keeps the section narrow but complete: validate provider configuration, validate state before provider trust, map accepted provider identity to local account policy, delegate accepted users to session creation, and verify that every negative path avoids session creation and sensitive logging.

## Traceability Notes

REQ-001 is intentionally broad enough to cover the valid callback path and the required negative paths. The research supports that requirement by tying each behavior to a concrete implementation owner and test surface. The plan should keep every callback behavior traceable through `codex-plan.md`, `codex-plan-tdd.md`, `sections/section-01-oauth-foundation.md`, and `tests/auth/test_oauth.py`.

The traceability matrix should not mark REQ-001 as implemented until the section is recorded complete. Before implementation, the status is planned or covered. After implementation, `implement-record-section` refreshes the matrix so the status follows recorded section completion rather than relying on a manual edit. That matters because the planning record is used later to explain why a section was considered complete.

This example also validates Forge itself. The fixture should remain complete enough that strict gates can distinguish real planning from setup stubs. Research, review, integration notes, TDD detail, sections, governance, and traceability all need concrete content because future runs may inspect these files independently of the chat that created them.

The final research conclusion is that the section is ready only when those artifacts agree on the same ownership boundary, failure modes, and verification command.

## Commands

- `uv run pytest`
- `python3 scripts/zagrosi_skills.py forge-score --planning-dir examples/saas/01-authentication --depth standard --strict`
