# Deep Review Example Track

This track contains starter material for exercising Forge in `deep` mode. It is
not included in `eval-suite` rows because it is an input scenario rather than a
completed planning fixture. Use it when comparing Forge against other planning
systems on a problem that needs migration strategy, rollback design, data
integrity review, implementation feasibility review, and context-resume
discipline.

Expected deep-mode output should include:

- a 5,000+ word `codex-plan.md`
- a 2,000+ word `codex-plan-tdd.md`
- a six-perspective review board under `reviews/`
- 1,500+ word section files with concrete file ownership and tests
- `codex-integration-notes.md` explaining accepted, rejected, and deferred
  review findings
- strict passes for `lint-plan`, `lint-sections`, `traceability`,
  `lint-artifact-schema`, `lint-review-integration`, and `forge-score`

The scenario deliberately stresses context compaction: later turns should be
able to resume from durable files without relying on hidden chat memory.
