# Installer PR reconciliation — 2026-09-24

## Decision

Do not merge or wholesale cherry-pick [PR #3](https://github.com/zagrosi-code/zagrosi-forge/pull/3).
Its installer remains unfinished, and it predates the current runtime split and
workflow improvements. Extract the small, applicable safety contracts below into
the current installer, then retire the draft as a superseded delivery branch.
Preserve its commit as reference; do not claim all its proposed capabilities shipped.

## Compared authority

| Tree | Commit | Git tree |
| --- | --- | --- |
| Current main | `204fcb493d56c3c9bb7947ecb2c4faa51d53f053` | `996a9a77ac2be054657535a883e85272f7898c0e` |
| PR #3 | `e78526e467479aab56ca8909aab266c00fd1e262` | `1b9161fcac7977fa6277e1236612eccf8a0db049` |
| Common ancestor | `b6c2e4fa17a6c853668f76d749856e8d48bf4889` | `14fdc2012b9044078acdf41fa54b94f5dcfe8aa3` |

GitHub readback: open, draft, conflicting, base `main`. Neither compared head is
the other's ancestor; there are 9 main-only and 90 PR-only commits. Main has no
`src/zagrosi_forge` package. PR #3 adds 42,284 Python lines across 17 installer
modules, plus 5,747 vendored Python lines. These are source counts, not installed
footprint or runtime-cost measurements.

Its `src/zagrosi_forge/install/__init__.py:26` exposes only `--version`; installer
commands return `package.feature_unavailable` until the Section 06 adapter exists.
An apparently comprehensive library is therefore not a complete replacement for
the working install command.

## Already covered on main

| Contract | Current evidence | Disposition |
| --- | --- | --- |
| Aggregate exact ownership fences; reject malformed fences | `scripts/forge/ownership.py`; `tests/test_patch_scope.py:69`; PR branch `f82de3c`, main `6abd17f` adds further malformed-fence cases | Preserve current implementation; do not reapply old monolith patch. |
| Exclude known development junk; reject symlink/nonregular package members; verify staged content; serialize cache publication; recover the previous cache after interruption | `scripts/forge/plugin_cache.py:18`, `:78`; `tests/test_cache_publication.py` | Keep current smaller cache mechanism. Its lock covers the cache, not the configuration update. |
| Bind runtime modules to verified bytes before execution; reject links, oversized/FIFO modules, injected modules and stale bytecode | `scripts/zagrosi_skills.py:104`; `tests/test_runtime_binding.py` | Preserve the source-bound loader; PR #3 predates this split. |
| Bounded child output/process cleanup and durable detached-record recovery | `scripts/forge/processes.py`; `tests/test_native_process.py`; `tests/test_transaction_publication.py`; `tests/test_transaction_rollback.py` | Existing protections have a different authority domain. They do not establish installer configuration atomicity. |

## Extract these contracts first

1. **Preserve valid configuration and publish it safely.**
   `installation.py:130–177` matches TOML headers as literal strings;
   `:413–425` reads and directly overwrites configuration outside the cache lock.
   A controlled probe using `[marketplaces.'zagrosi']` produced an invalid
   duplicate table after updating. Port the *requirements* represented by PR #3
   `tests/install/test_codex_config.py`: quoted/dotted/inline table equivalence,
   unrelated-value preservation, parse failure before mutation, identity/drift
   checks, restrictive same-directory staging and atomic replacement. Prefer a
   bounded current implementation; do not import its entire ownership/journal
   framework. If the minimal writer cannot safely preserve a legal representation,
   refuse it without changing bytes and direct users to the native plugin installer.
   Regressions must cover alternate syntax, malformed input, interruption,
   concurrent updates, permission preservation and replacement failure.

2. **Keep configuration previews free of unrelated values.**
   `installation.py:503–504` emits the full proposed configuration. A synthetic
   canary in an unrelated key survives into that preview. Report only the three
   managed changes and their destinations. Adapt PR #3's
   `test_secret_config_canary_never_reaches_result_or_snapshot`; use synthetic
   input and assert the canary is absent from every output/error path.

3. **Define package membership explicitly.**
   `plugin_cache.py:14–55` uses a denylist. A synthetic root `.env` changed its
   fingerprint and was not excluded: arbitrary local files can enter the cache.
   Extract the explicit inventory and closed-reference expectations from PR #3
   `bundle.py:878–901` and `tests/package/test_bundle_contract.py`, adapted to the
   current runtime and installed verification tools. Do not copy its stale member
   list. Dirty-source tests should prove secret canaries, unknown files, linked
   files and development artifacts never enter the published package, while every
   skill reference and required runtime/verification file remains usable.

## Defer rather than silently discard

The PR's wheel/sdist/archive reader, tool acquisition, vendored TOML editor,
receipt/adoption system and cross-platform durable transaction journal are
separate product scope. The current supported flow installs a local plugin tree;
it does not read those archives or expose that new installer CLI. Keep the old
commit as design/test evidence if distribution requirements later justify them.
Do not resurrect old dependency/action versions or its oversized serial CI
workflow while extracting tests.

## Verification and limits

Read-only comparison; no PR mutation, configuration change or branch merge.
Ran the three synthetic probes and 49 tests from an isolated `git archive` of
`204fcb4`: `test_installation`, `test_cache_publication`, `test_runtime_binding`
and `test_patch_scope` all passed in 5.99 seconds. The probes reveal gaps outside
those existing tests. No PR #3 full suite, new-platform test or new fix is claimed.
The archive avoids concurrent development changing the audited source.
