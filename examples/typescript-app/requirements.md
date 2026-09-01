# TypeScript App Requirements

Build one shared user/session boundary for OAuth and account settings.

- REQ-001: Valid OAuth callbacks create sessions after state validation.
- REQ-002: Authenticated users can update display preferences.
- Invalid state, provider denial, replay, and ambiguous accounts create no session.
- Unauthenticated updates fail before validation or writes.
- Tests use local fixtures; never call providers or use real secrets.

Assume `src/auth`, `src/settings`, Vitest, and `npm test`; adapt names to local conventions. Keep callback, session, and preference-write logic separate. Billing, teams, profiles, and unrelated UI are out of scope.
