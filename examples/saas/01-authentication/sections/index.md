<!-- PROJECT_CONFIG
runtime: python-uv
test_command: uv run pytest
END_PROJECT_CONFIG -->

<!-- SECTION_MANIFEST
section-01-oauth-foundation
END_MANIFEST -->

# Implementation Sections Index

## Reader Note

This index is self-contained for the SaaS authentication split. It defines the
section boundary, dependency graph, execution order, parallelization decision,
verification command, and handoff expectations. The split has one section
because REQ-001 is cohesive: provider configuration, callback validation, local
account policy, and session creation must be reviewed as one security boundary.

## Dependency Graph

| Section | Depends On | Blocks | Parallelizable |
|---------|------------|--------|----------------|
| section-01-oauth-foundation | - | 02-billing, 03-dashboard | No |

`section-01-oauth-foundation` has no prior section dependency inside this
split. It blocks later project splits because billing and dashboard features
need a stable authenticated user/session contract. It is not parallelizable
inside the auth split because the same files define config validation,
callback behavior, session creation, and tests.

## Execution Order

1. Run `section-01-oauth-foundation`.
2. Write pytest tests first in `tests/auth/test_oauth.py`.
3. Implement config validation in `src/auth/config.py`.
4. Implement callback orchestration in `src/auth/oauth.py`.
5. Reuse or add session creation in `src/auth/session.py`.
6. Run `uv run pytest tests/auth/test_oauth.py`.
7. Run the full configured command: `uv run pytest`.

## Section Summary

### section-01-oauth-foundation

Owns REQ-001 OAuth registration and sign-in. The section implements provider
configuration validation, callback state validation, provider denial handling,
provider identity resolution, local account policy, safe logging, and session
creation through the existing session module. It must not implement billing,
dashboards, account-link UI, or provider administration.

## Parallelization Notes

Do not split this section across independent workers unless ownership is
explicitly coordinated. `src/auth/oauth.py` and `src/auth/session.py` are
security-sensitive and tightly coupled through the session creation contract.
Parallel edits would increase the risk of a callback path creating sessions
without the same checks that tests expect. If parallel work is required, one
worker may draft tests while another inspects existing auth conventions, but
only one worker should own production auth changes.

## Handoff Standard

The section file must copy the relevant requirement, contracts, test names,
fixtures, files, risks, rollback notes, and final verification commands. It
should not depend on the implementer reading this index or the full plan for
essential behavior. The section should be specific enough that a fresh
implementer can run the tests, build the callback flow, and know when to stop.

## Risk Register

The primary risk is creating a session after invalid state, provider denial,
disabled provider config, or ambiguous account resolution. The secondary risk
is leaking authorization codes, access tokens, refresh tokens, cookies, or
provider profile payloads through logs and errors. A third risk is quietly
adding a new session mechanism that billing and dashboard code cannot trust.
The section mitigates these risks through tests-first work, structured result
contracts, and single-module session ownership.

## Completion Evidence

Completion notes should include the targeted pytest result, full pytest result,
any lint or type-check result discovered in the repository, the final files
modified, and a short note about account-linking policy. If the repository did
not have an account-link policy, the expected implementation is a structured
`ambiguous_account` result, not silent linking.

## Adaptation Rules

If real repository paths differ, preserve the requirement ownership and record
the adapted paths. For example, a FastAPI project may place route adapters in
`src/api/auth.py`, while the callback service lives in `src/auth/oauth.py`.
A Django project may use `accounts/services/oauth.py` and
`accounts/tests/test_oauth.py`. Those adaptations are acceptable when the
section still preserves one provider config boundary, one callback boundary,
one session boundary, and one REQ-001 test matrix.

The index intentionally keeps a single section. If implementation reveals that
schema migration, account-link UI, or provider administration is required, do
not make this section enormous. Create a follow-up split or section and keep
this one focused on the foundation that can be verified safely.

## Verification Sequence

The run order is tests-first, targeted verification, full verification, then
governance update. Do not mark the section complete after only the targeted
test file if the repository has broader auth tests. The full command catches
session regressions in existing login paths and prevents the OAuth foundation
from breaking behavior that billing and dashboard splits will rely on.

After verification, update traceability and decisions before moving to later
splits. This keeps the example honest: implementation output is not just code,
but code plus evidence that REQ-001 is covered and ready to become a dependency.
