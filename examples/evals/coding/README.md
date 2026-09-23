# Coding trials

Six isolated cases cover an ordinary summary feature, behavior-preserving cleanup,
deep discount design, a real Forge resume checkpoint, an order-dispatch godfile,
and a Node summary feature. The invoice cases preserve public APIs, exact exports, rounding, and
errors. The godfile case exercises cohesive module extraction while preserving
validation, pricing, shipping, serialization, file access, and order transitions.
The ordinary summary request never asks for refactoring: independent review checks
whether relevant duplication was cleaned up along the way. Its unrelated
`src/legacy_reports.py` is protected; editing it fails scope even if tests pass.
The Node case uses CommonJS, `node --test`, and an independent Node oracle without
a framework. Node must be available to run it.

```bash
python3 tools/coding_trials.py prepare /tmp/forge-summary --case summary
# Give /tmp/forge-summary/prompt.md to an agent; edits stay in its workspace.
python3 tools/coding_trials.py check /tmp/forge-summary
```

`--depth lean|standard|deep` overrides the case's default, enabling the same case
at every depth. For unattended trials, `run ... --runner EXECUTABLE ARGS...` starts
an agent in the workspace, sends the prompt on stdin, times it, and checks its
output. Use a runner that accepts this contract. Default timeout: 600 seconds.
An existing trial is never overwritten. Use a fresh directory for each run.
Output is drained continuously; only the last 12,000 bytes of each stream are
retained, with byte counts and truncation flags. Timeouts retain available output
and return 124. POSIX timeouts kill the process group, including children that
outlive the leader. Windows uses `taskkill /T /F`; unproven termination is reported.

The checker runs existing/added tests plus an independent oracle outside the
editable workspace. It compares hundreds of legacy outputs and new feature
cases. Each candidate call receives a fresh input copy; expected values are
computed first, and input mutation is checked separately. Reports include scope changes, source/branch/function sizes, repeated
loops, external imports, test output, and runner time. Python AST metrics are not
ported by guesswork: JavaScript complexity/dependency metrics remain unknown. These structural measures
support review; they do not establish readability or reward code golf.

## Verdicts and cleanup review

Results keep three decisions separate:

- `behavior`: the independent oracle completed and existing/added tests passed.
- `workflow`: Forge admits the actual `.planning` plan with strict checks at the
  selected trial depth, then verifies implementation completion records. A narrative
  "done" note is insufficient.
- `cleanup`: for ordinary summary, cleanup and godfile cases, source syntax must change, and an
  independent reviewer must confirm useful cleanup with concrete changes and
  before/after regression evidence. Existing tests and the independent oracle can
  supply sufficient coverage; add tests where coverage is weak. Test changes are
  reported separately. Other cases report `not_required`.

An unchanged cleanup fixture can pass behavior, but cannot pass the overall trial.
Comments, formatting, docstrings and empty new modules do not establish cleanup.
Syntax differences establish that code changed; they do not judge its usefulness.
No line-count, complexity, or readability threshold determines success.

After the candidate finishes, an independent reviewer can create an evidence form:

```bash
python3 tools/coding_trials.py review-template /tmp/forge-cleanup > /tmp/forge-cleanup/review.json
# The independent reviewer inspects the baseline, candidate, and test evidence,
# then fills reviewer, independent, verdict, and the cleanup fields in review.json.
python3 tools/coding_trials.py check /tmp/forge-cleanup --review /tmp/forge-cleanup/review.json
```

The template starts pending and grants no approval. Keep it outside the candidate
workspace. Preserve its baseline/candidate fingerprints; source or test edits
invalidate the review. Set `cleanup.changed_files` to the implementation paths
whose substantive changes were reviewed, and explain their usefulness in
`cleanup.rationale`. `cleanup.regression_evidence` identifies the checks observed
before and after cleanup. The overall verdict also requires workflow completion,
unchanged evaluators/plugin provenance, allowed scope, and a successful runner.
An unattended cleanup run therefore remains incomplete until this review is supplied.

Reviewer identity and independence are explicit attestations from the trial
operator; JSON does not authenticate people or agents. These trials are not a
security sandbox. Deliberately detached process sessions may escape group cleanup.
Windows process-tree termination needs platform validation; the integration tests
exercise POSIX process groups.

## Provenance and measurements

Preparation fingerprints the launcher, all runtime Python modules under
`scripts/forge`, and skill Markdown. Checking reports plugin drift separately from
behavior. Older records without runtime coverage report `incomplete`, not verified;
prepare a fresh trial for a complete comparison. Oracle and fixture drift also
invalidate the result. The actual runtime still performs its own integrity check.

`--telemetry FILE` attaches runner-reported model/token/retry data unchanged,
separately labeled. Missing telemetry remains unknown. Compare the same case,
depth, model, tool access, and fixture bytes; repeat runs before claiming quality
or speed improvements. Manually review cleanup usefulness and unnecessary layers.
Resume preparation admits a compact plan at the selected depth, runs real
`implement-setup`, observes the missing item_count regression fail, and persists an
`implement-progress --stage red` checkpoint with source/contract snapshots. The
agent resumes through normal Forge status; the failing test must pass before
completion. The original red event must remain first in history, and the prepared
regression must retain its semantics and pass an explicit, unskipped test run. New
tests, source changes and appended progress events are allowed. Trial metadata
outside the workspace binds both original identities. This does not simulate killing an agent process. Runtime crash
recovery has a separate fault-injection suite.
