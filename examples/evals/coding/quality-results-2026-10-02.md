# Quality observations — 2 October 2026

**Both arms accepted 6 of 8 attempts. Forge took more time and tokens.** Blinded
review preferred Forge in three pairs, plain code in two, and tied three. This
small experiment does not establish general quality or speed superiority.

[Machine-readable results](quality-results-2026-10-02.json) contain every attempt,
source identity, reviewer judgment, usage field and limitation. Earlier reports
remain unchanged.

## Frozen comparison

- Source and evaluator: `8293a95268818db6ce8745150fb1a5423de51b4e`, tree
  `00b2dc996a6e53e15f55e67bae70d44be0b710e7`; frozen files rechecked before publication.
- Codex CLI 0.154.0; requested `gpt-5.5`, medium effort; macOS 26.6.2 arm64,
  Python 3.14.7, Node 24.18.0, TypeScript 7.0.2. Backend model revision is unverified.
- Four task families, two fresh repetitions per arm, standard depth, rotating
  arm order, serial execution, 900-second deadline per writer. All 16 writers
  finished without timeout. No corrective prompts or candidate edits by the operator.
- One immutable evaluator checked behavior, scope, required cleanup and Forge
  completion. Plain-agent workflow records are inapplicable. TypeScript candidates
  also compiled against the immutable public consumer before 394 runtime assertions.
- Three fresh reviewers inspected eight shuffled packets without arm identities,
  logs, timing or usage. All eight reviews validated. Code can reveal workflow
  fingerprints; blinding is partial and independence is an attestation.

To repeat the configuration, run from that evaluator snapshot after installing
its locked development compiler:

```bash
npm ci --prefix tools
python3 tools/trial_matrix.py compare /path/to/fresh-comparison \
  --plugin-root /path/to/same-frozen-snapshot \
  --model gpt-5.5 --effort medium \
  --cases summary retry-queue godfile typescript-access \
  --depths standard --repeats 2 --timeout 900
```

Then prepare and independently review the blinded packets as described in the
[trial guide](README.md#controlled-forgeplain-comparison). Preserve failed attempts.
Exact repetition is not guaranteed by a requested model alias.

## Acceptance and code review

| Task | Forge accepted | Plain accepted | Blinded preference, repetitions 1/2 |
|---|---:|---:|---|
| Summary feature plus encountered cleanup | 1/2 | 0/2 | Forge / Forge* |
| Webhook retry bug fix | 2/2 | 2/2 | Tie / Tie |
| Order-dispatch module extraction | 1/2 | 2/2 | Plain / Plain |
| TypeScript policy/transaction cleanup | 2/2 | 2/2 | Tie / Forge |
| **Total** | **6/8** | **6/8** | **3 Forge, 2 plain, 3 ties** |

*Preference and acceptance are separate. The second summary pair passed behavior,
but neither candidate simplified the encountered duplication; both failed the
required cleanup check. The reviewer slightly preferred Forge's compatibility tests.

- **Compatibility failure:** the first Forge order-dispatch candidate passed its
  own tests but dropped public imported names, including `Path`. The independent
  oracle and reviewer rejected it. The other 15 candidates passed behavior.
- **Missing cleanup:** both plain summary candidates and the second Forge summary
  candidate left the repeated invoice arithmetic intact. Feature correctness alone
  did not establish cleanup. The first Forge summary extracted shared arithmetic.
- **Cohesion:** reviewers preferred plain dispatch decompositions; the second Forge
  version introduced a delayed reverse import through its stateful order book.
  Both arms produced identical minimal retry fixes. TypeScript preferences were
  modest, with gaps in candidate tests for defensive-copy identity noted explicitly.
- **Review integrity:** five initial forms listed test files under implementation
  changes and were rejected. Their original reviewers corrected that field only;
  judgments, candidate bytes and acceptance rules stayed unchanged. Initial forms
  and failed validation are retained. Reviewers inspected supplied check receipts;
  they did not rerun tests or establish when characterization tests were written.

## Cost of accepted work

All eight attempts in each arm count, including failures. Elapsed time includes
preparation, writer execution and initial automated checks; independent review and
later review-application checks are excluded.

| Measure | Forge | Plain |
|---|---:|---:|
| Total attempt time | 2,903.219s | 1,149.684s |
| Seconds per accepted result | 483.9 | 191.6 |
| Uncached input tokens per accepted result | 84,236 | 29,158 |
| Cached input tokens per accepted result | 1,445,461 | 326,464 |
| Output tokens per accepted result | 19,728 | 8,645 |
| Reported financial cost | Unknown | Unknown |

Token figures come from the CLI and are rounded here; cached input is part of
total input. Instrumented intervention totals were unavailable; the operator
separately attests zero candidate corrections. Token counts are not subscription
charges. Command-phase observations in the JSON are event-receipt measurements,
not exact phase duration, CPU time or model thinking.

These workspaces inherited an ignored outer Git repository. Traces showed extra
repository discovery and fallback diff commands. Fresh trials now receive their
own baseline repository, but this frozen comparison does **not** measure that
later change. Some local development/checks overlapped attempts; timing also
includes uncontrolled host contention and model-service variability.

## Interrupted continuation

A separate snapshot, `f872c9c1c323a896c32becfd59bcc5c17f574f44`, includes the
isolated Git baseline. Three Codex-only trials started from an admitted plan and
saved failing test. Each first process was killed after 10 seconds with confirmed
cleanup; one fresh session then had 900 seconds to finish. All three preserved
the original regression/checkpoint and passed behavior, scope and workflow checks.

| Depth | Accepted | Total attempt time | Deliberate interventions |
|---|---|---:|---:|
| Lean | Yes | 232.591s | 1 |
| Standard | Yes | 163.278s | 1 |
| Deep | Yes | 164.968s | 1 |

Interrupted-session usage was incomplete, so aggregate tokens/cost remain unknown.
These isolated CLI checks did not stage packages in the native plugin cache and
do not establish native plugin discovery, Claude/Gemini model access or cross-host
handoff. Native sign-in-dependent checks remain pending.

## CI feedback, measured separately

| Windows measure | Before | After partitioning |
|---|---:|---:|
| Observed critical path | 21m02s | 13m04s |
| Aggregate runner time | 21m02s | 22m22s |
| Passing shared+portable tests | 1,257 | 1,357 |
| Skipped tests | 39 | 39 |

[Before CI](https://github.com/zagrosi-code/zagrosi-forge/actions/runs/36872690815)
ran on `c6075892eaac4095a9a04049752f4150ae51fd95`;
[partitioned CI](https://github.com/zagrosi-code/zagrosi-forge/actions/runs/37007325632)
ran on the comparison source. Each passed all its jobs; original collected test
coverage was retained. These are single runs on different sources, test sets and
allocated runners. The shorter observed wait does not establish lower compute
cost or faster model-driven work.

The next efficiency investigation should target repeated context and workflow
work while retaining public-contract checks. These observations justify neither
a blanket performance claim nor removal of the checks that caught real failures.
