# TypeScript App Requirements

Build the authentication and account-settings foundation for a small
TypeScript application. The work should prove OAuth callback safety and
authenticated preference updates without introducing billing, team management,
or unrelated profile features.

## Product Context

The app needs a single user/session boundary that route handlers and feature
modules can share. OAuth callback handling creates authenticated sessions.
Account preferences reuse the same session lookup to prove later features do
not invent their own auth checks. The example is intentionally small, but the
planning artifacts should still carry production-grade detail.

## Requirements

- REQ-001: Valid OAuth callbacks create sessions after state validation.
- REQ-002: Authenticated users can update display preferences.
- Invalid callbacks, provider denial, replayed callbacks, and ambiguous
  accounts fail without creating sessions.
- Unauthenticated preference updates fail before payload validation or writes.
- Tests use local fixtures and do not call real providers or use real secrets.

## Constraints

Use local TypeScript and test conventions in the target repository. The example
assumes `src/auth`, `src/settings`, Vitest, and `npm test`, but those are
adaptable. The auth and settings boundaries are not adaptable: callback logic,
session logic, and preference writes should stay separate.
