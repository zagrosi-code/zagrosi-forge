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

- a [canonical compact plan](../../skills/zagrosi-plan/references/plan-format.md)
  covering migration order, contracts, failure modes, data integrity, rollout,
  and rollback: one section for bounded work, a shared plan for multiple sections
- test design beside its requirements, with separate artifacts only for
  independently reused detail
- review by the active agent, with independent perspectives for concrete risks;
  keep findings and resolutions in the canonical plan
- section files with concrete ownership, dependencies, tests, stop lines, and
  rollback; split sections when ownership or risk boundaries diverge
- separate integration notes only when findings need independent ownership
- a passing `postflight --phase plan --depth deep --strict` for the planning
  directory; diagnose any failed gate before implementation

Remove repetition, narrative padding, and context already recoverable from the
source or repository. Add detail only when its absence would change a decision,
test, handoff, or recovery action.

The scenario deliberately stresses context compaction: later turns should be
able to resume from durable files without relying on hidden chat memory.
