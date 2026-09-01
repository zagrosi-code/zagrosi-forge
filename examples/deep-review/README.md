# Deep Review Example Track

This track contains starter material for exercising Forge in `deep` mode. It is
not included in `eval-suite` rows because it is an input scenario rather than a
completed planning fixture. Use it when comparing Forge against other planning
systems on a problem that needs migration strategy, rollback design, data
integrity review, implementation feasibility review, and context-resume
discipline.

Deep mode has no word-count floor. Depth comes from evidence and resolved
decisions; every artifact stays bounded to information needed to implement and
verify the change. Expected output should include:

- a focused `codex-plan.md` covering migration order, contracts, failure modes,
  data integrity, rollout, and rollback
- a separate `codex-plan-tdd.md` only when it adds material test design beyond
  the implementation plan
- a bounded review set under `reviews/` covering only material perspectives
- section files with concrete ownership, dependencies, tests, stop lines, and
  rollback; split sections when ownership or risk boundaries diverge
- integration notes only when accepted, rejected, or deferred findings cannot
  be represented clearly in the plan or review
- strict passes for `lint-plan`, `lint-sections`, `traceability`,
  `lint-artifact-schema`, `lint-review-integration`, and `forge-score`

Remove repetition, narrative padding, and context already recoverable from the
source or repository. Add detail only when its absence would change a decision,
test, handoff, or recovery action.

The scenario deliberately stresses context compaction: later turns should be
able to resume from durable files without relying on hidden chat memory.
