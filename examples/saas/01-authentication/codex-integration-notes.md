# Review Integration Notes

## Reader Note

These notes record how the architecture review was integrated into the SaaS
authentication plan. They are intentionally explicit so `lint-review-integration`
can distinguish accepted findings, rejected findings, deferred scope, and plan
edits. The purpose is not to restate the whole plan. The purpose is to prove
that review changed the artifacts in a traceable way.

## Accepted Review Items

### Accepted: Route Adapter Boundary

The architecture review found that the plan needed a clearer distinction
between framework route adapters and OAuth business logic. This was accepted.
The plan now includes route adapter guidance explaining that a route may parse
callback query parameters, load saved state from request context, call provider
configuration validation, call the OAuth callback service, and translate the
structured result into an HTTP response. The route must not own state
comparison, account lookup rules, cookie signing, or token logging decisions.

The section file also reflects this change. `section-01-oauth-foundation.md`
now says framework-specific route files are allowed but should be adapters.
REQ-001 decisions remain in the auth service so pytest can exercise them
without a live server.

### Accepted: Identity Persistence Stop Line

The architecture review found that durable provider identity storage needed to
be a stronger stop line. This was accepted. The plan now says to stop and
re-plan if the repository has no durable way to relate provider identity to
local accounts. The normalized spec already warned against inventing a partial
migration inside callback code; the plan now makes the same risk operational.

This change affects implementation readiness. A real implementer must inspect
the user repository or external identity model before making the valid callback
test green. If the storage boundary is absent, the correct next step is a data
migration plan, not a local in-memory shortcut.

### Accepted: Idempotency And Replay Boundary

The review found that retry and replay behavior should be more explicit. This
was accepted. The plan now includes data consistency and idempotency guidance:
state consumption is the callback idempotency boundary, replay must not create
another session or account, and account creation plus provider identity linking
should use the repository's transaction pattern when separate writes are
required.

The TDD plan already includes invalid state and provider denial tests. The
section keeps the expectation that replay behavior is tested when the target
repository has a state-consumption helper. If no durable replay store exists,
the implementation should model state consumption through an injected
dependency and leave durable storage to a follow-up migration or state-store
section.

## Rejected Review Items

### Rejected: Adopt A New Auth Framework

The review considered recommending a full auth framework. This was rejected.
The plan is meant to adapt to existing repositories. If the target repository
already uses an auth framework, the section should integrate with it. If it
does not, adopting a new framework changes too much surface area for this
section: routes, sessions, migrations, provider configuration, callback logic,
tests, docs, and rollback.

Rationale: framework adoption is a separate architecture decision and should
not be hidden inside REQ-001.

### Rejected: Split Provider Config Into Its Own Section

The review considered splitting provider configuration into a separate section.
This was rejected for the example because provider configuration validation is
small and tightly coupled to callback safety. Disabled providers, missing
provider fields, and unknown providers are part of the REQ-001 safety matrix.
Keeping those tests with the OAuth callback tests makes the foundation easier
to review.

Rationale: a separate config section would add sequencing overhead without
meaningful implementation isolation for this example. A real repository with a
provider-admin subsystem can split it later.

## Deferred Items

Route-level integration testing is deferred until a real repository exposes a
framework test harness. The pure OAuth tests remain mandatory. A small route
test is recommended after the service-level matrix passes, but the example does
not force a framework-specific testing style.

Account-linking UI is also deferred. Ambiguous accounts return a structured
`ambiguous_account` result unless explicit product policy already exists. UI
for resolving that ambiguity is a separate product plan.

## Plan Edits Made

The accepted changes were integrated into `codex-plan.md` under route adapter
guidance, data consistency and idempotency, implementation stop lines, and the
completion runbook. The implementation section was updated to keep framework
route files as adapters and to preserve service-level REQ-001 tests. The TDD
plan remains aligned with the reviewed risks: invalid state, provider denial,
disabled configuration, missing configuration, ambiguous accounts, and token
logging.

## Verification

The review integration is complete when `lint-review-integration --strict`
passes, `lint-plan --depth standard --strict` still passes with the review file
present, and traceability still maps REQ-001 through the normalized spec, plan,
TDD plan, implementation section, and `tests/auth/test_oauth.py`.

## Residual Risk Handling

Any residual risk found during implementation should be handled through the
governance files, not by editing the plan silently after the fact. If route
integration coverage is missing, add a risk entry that names the route file and
the skipped test harness. If provider identity persistence is missing, stop and
create a migration plan. If account-linking policy is unclear, preserve the
`ambiguous_account` result and defer product UI. If provider configuration
differs from the example, record the actual config file and loader in
`decisions.md`.

This integration approach keeps the original plan stable while still letting
the implementation record real repository facts. The reviewed intent remains
auditable, and the implementation notes explain where the target codebase
required adaptation.

## Completion Evidence Expected

Implementation completion should cite concrete evidence for each accepted
review item. For the route adapter boundary, cite the adapter file and the test
that proves callback policy lives in the service. For identity persistence, cite
the account or external identity repository used by the valid callback path.
For idempotency, cite the state consumption helper, replay test, or explicit
follow-up section if durable state is outside the current scope. These evidence
lines make the accepted review findings durable after context compaction because
a later Codex turn can reconstruct which review decisions changed the plan,
which decisions were rejected with rationale, and which repository facts still
need implementation notes before the section can be considered complete.
