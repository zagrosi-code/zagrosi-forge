<!-- PROJECT_CONFIG
runtime: node-vitest
test_command: npm test
END_PROJECT_CONFIG -->

<!-- SECTION_MANIFEST
section-01-auth-flow
section-02-preferences
END_MANIFEST -->

# Sections

## Dependencies

| Section | Depends On | Blocks | Parallel |
|---|---|---|---|
| section-01-auth-flow | - | section-02-preferences | No |
| section-02-preferences | section-01-auth-flow | - | No |

## Execution order

1. Auth callback/session contract.
2. Preferences after shared lookup exists.

No concurrent production edits unless the `src/auth/session.ts` interface and file ownership are agreed. Section 1 owns callback/session creation; section 2 owns settings and consumes lookup. Verify targeted tests, then `npm test`; also discovered `npm run lint`/`npm run typecheck`. Risks: invalid-session creation, auth bypass, secret logging.
