# Architecture Review

## Summary

The authentication plan has a strong ownership model for REQ-001. Provider
configuration is assigned to `src/auth/config.py`, OAuth callback decisions are
assigned to `src/auth/oauth.py`, and session creation is assigned to
`src/auth/session.py`. That split is appropriate because it keeps provider
semantics, account policy, and cookie/session persistence independently
reviewable. It also gives later billing and dashboard work a stable local
session contract rather than forcing those features to understand OAuth
callback details.

The plan is implementation-ready, but the review identified three architecture
items that should be made explicit before coding: the route adapter boundary,
the account identity persistence boundary, and the transaction or idempotency
boundary for first-time registration. These are not reasons to reject the plan.
They are areas where a real repository often hides complexity, and they deserve
clear wording in the plan and section.

## Accepted Findings

### Finding A1: Route Adapter Boundary

Severity: Medium.

The plan names `src/auth/oauth.py` as the callback orchestration owner, but a
real SaaS service will still have a framework route. If the route parses query
parameters, applies cookies, and redirects users, it can easily accumulate
business logic unless the plan states that route files are adapters. The plan
should explicitly say that route code may parse request data and translate the
structured callback result into an HTTP response, but it must not own state
comparison, provider denial mapping, account linking policy, token logging, or
session signing.

Accepted change: the implementation plan now includes route adapter guidance.
The section file also instructs implementers to keep framework-specific request
parsing at the edge and keep REQ-001 decisions in the auth service. This makes
the route test surface smaller and keeps the callback matrix in deterministic
unit tests.

### Finding A2: Local Identity Persistence Boundary

Severity: High if missing in a target repository, Medium for this example.

The plan correctly stops when a repository lacks durable external identity
storage, but the first draft did not make this visible enough as an
implementation stop line. OAuth registration is unsafe if the provider identity
is accepted only for the current session and cannot be resolved on the next
login. The plan should require implementers to inspect existing local account
and external identity models before implementing successful registration.

Accepted change: the implementation stop lines now say to pause if no durable
way exists to relate provider identity to local accounts. This protects real
repositories from shipping a demo-only success path that cannot survive
returning sign-in.

### Finding A3: Idempotency And Replay Boundary

Severity: Medium.

The plan already requires state validation and replay rejection. The review
recommends stronger wording about first-time account creation and provider
identity linking as an idempotent operation. Browser retries and provider
redirect retries are normal. A callback should not create duplicate local users
or duplicate links if the state or callback is replayed. If account creation
and provider identity linking happen in separate writes, the plan should
recommend a transaction or repository-level idempotency pattern.

Accepted change: the plan now includes data consistency and idempotency
guidance. It states that state consumption is the callback idempotency boundary
and that account creation plus provider identity linking should follow the
repository's transaction pattern.

## Rejected Or Deferred Findings

### Finding A4: Introduce A Full Auth Framework

Severity: Low.

The review considered whether the plan should recommend adopting a full auth
framework. This was rejected for the example. The package is designed to adapt
to an existing repository, not prescribe a framework migration. If a target
repository already uses an auth framework, the plan should integrate with it.
If not, introducing one is too broad for the authentication foundation section.

Rationale: adopting a framework changes routes, sessions, migrations, callback
handlers, provider configuration, tests, and documentation. That is a separate
architecture decision and should not be hidden inside REQ-001.

### Finding A5: Split Provider Config Into A Separate Section

Severity: Low.

This was rejected for the example because provider configuration is small and
security-coupled to callback behavior. Splitting it out would create a section
dependency without enough implementation value. The plan should keep config
validation in the same authentication foundation section unless a real
repository has a large provider-admin subsystem.

## Required Plan Edits

The plan should contain a visible route adapter section, a visible identity
persistence stop line, idempotency guidance, and clear acceptance criteria that
session creation always flows through `src/auth/session.py`. The TDD plan
should include tests that fail if invalid state reaches provider work, provider
denial creates a session, ambiguous account linking falls through, or sentinel
secrets appear in logs.

## Section Impact

`section-01-oauth-foundation.md` should remain a single section. The reviewed
scope includes four files: `src/auth/config.py`, `src/auth/oauth.py`,
`src/auth/session.py`, and `tests/auth/test_oauth.py`. If implementation in a
real repository reveals additional route files, those can be adapters and must
be recorded in completion notes. If implementation reveals migrations or
account-link UI, that should become a new section or split.

## Verification Impact

The architecture review does not require additional provider integration tests
before service-level tests pass. It does require the final implementation notes
to record which route adapter, if any, calls the OAuth service. It also requires
the traceability matrix to continue mapping REQ-001 to the plan, TDD plan,
section, and `tests/auth/test_oauth.py`.

## Follow-Up Audit Questions

Before implementation starts in a real repository, the implementer should
answer four audit questions in completion notes. First, which file actually
owns session creation today, and did the OAuth flow delegate to it? Second,
which file or repository object owns provider identity persistence, and is it
durable across returning sign-in? Third, which route adapter calls the OAuth
service, and does it contain only request/response translation? Fourth, which
test proves invalid state does not reach provider work? These questions are
deliberately operational. They force the implementation to connect the design
to code evidence rather than treating the plan as a prose artifact.

If any answer is unavailable, that is residual risk and should be recorded in
`risk-register.md`. The section can still proceed only if the missing answer is
outside REQ-001 or explicitly accepted by the user. Missing session ownership,
missing provider identity persistence, or missing invalid-state test coverage
should block completion.
