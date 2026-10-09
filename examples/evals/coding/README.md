# Coding trials

[Latest comparison](behavior-contract-results-2026-10-02.md): 36 attempts across
four independently authored task families. Previous/new Forge accepted 8/12 each;
plain Codex accepted 9/12. The separate preservation audit records valid final
pairs, rebuilt histories and coverage limits; no overall quality gain is established.
The [twelve-attempt follow-up](quality-followup-2026-10-02.md),
[earlier sixteen-attempt experiment](quality-results-2026-10-02.md) and interruption
checks remain unchanged.

## Manifest-driven suites

Suite mode compares any declared product arms, including a plain arm, on the same
frozen task, dependencies, native tests and independent oracle. Paths such as
`backend/app` are supported; Forge depth and workflow requirements are explicit
arm settings. Preparation freezes the evaluator and input files. Writers receive
fresh workspaces; assessment uses a separate frozen delivery snapshot.

This is an opt-in evaluation tool. Supply a curated `coding-trial-suite/v1`
manifest and its resources; there is no bundled, qualified prospective study.
Synthetic fixture tests exercise the implementation. They do not establish native
model loading, comparative code quality or performance. Historical results above
retain their original evaluator and interpretation.

### Choose a mode

| Purpose | Execution and evidence |
|---|---|
| `synthetic` | A declared fixture command; no provider calls. Missing qualification is `unmeasured`. A fixture writer can run without isolation, but candidate assessment requires an explicit Docker profile. Tests using execution doubles establish logical behavior only. |
| `prospective` | The v1 native recipe supports **Codex CLI 0.154.0** in a prepared, digest-pinned `docker-v1` image. It requires admitted environment/task evidence, fresh isolation checks and source-bound native loading evidence. Claude/Gemini are unsupported in this suite path; legacy Claude trials remain available below. |

The evaluator controller requires Python 3.11+ and Git. Native suite loading
and writer restoration additionally require a POSIX controller with
descriptor-relative no-follow filesystem operations; Windows native suite
execution is unsupported. This limit does not apply to ordinary portable
Forge workflows or process-free suite validation.

The prepared image must already contain the declared host, dependencies and
required tools, including `/usr/local/bin/python3`; Forge workflow validation also
needs Git. The evaluator does not pull/build images or resolve undeclared
dependencies. Each Docker candidate invocation receives a current isolation check;
private oracle code runs outside candidate containers and obtains observations
through the isolated worker. Arbitrary candidate imports in the private oracle
are unsupported. Docker limits, mounts, network and owned-container cleanup are
checked rather than inferred from a profile file.

Native images must also contain `/etc/codex/requirements.toml` with this
exact parsed local-only marketplace policy, checked by the evaluator's
[image readback](../../../tools/coding_trial_loading_evidence.py):

```toml
[marketplaces]
restrict_to_allowed_sources = true

[marketplaces.allowed_sources.evaluator]
source = "local"
path = "/marketplace"
```

### Commands

Run from the frozen evaluator checkout. Every `prepare`, `run` and `compare`
destination must be fresh. Standalone `prepare`/`run` also create a sibling
`<destination>.study`; both paths must be absent. A prepared directory is
inspectable setup; `run` does not resume or consume it.

```bash
# Replace the suite path and task/arm IDs with your curated inputs.
python3 tools/coding_trials.py prepare /tmp/suite-inspect \
  --suite /path/to/suite.json --task normalize --arm plain
python3 tools/coding_trials.py run /tmp/suite-attempt \
  --suite /path/to/suite.json --task normalize --arm plain

# Material review follows the completed behavior assessment.
python3 tools/coding_trials.py review-template /tmp/suite-attempt > /tmp/suite-review.json
# An independent reviewer fills the form, preserving its source identities.
python3 tools/coding_trials.py check /tmp/suite-attempt --review /tmp/suite-review.json

# All declared tasks × repetitions × arms, scheduled before any writer starts.
python3 tools/trial_matrix.py compare /tmp/suite-comparison --suite /path/to/suite.json
python3 tools/trial_matrix.py blind /tmp/suite-comparison
# Complete original blind/000001/B*/review.json forms for produced candidates.
python3 tools/trial_matrix.py apply-reviews /tmp/suite-comparison
python3 tools/trial_matrix.py report /tmp/suite-comparison
```

Native `run`/`compare` can explicitly add `--qualify-loading` to observe discovery,
selected-entry reading, required tools/task actions and declared subagents before
ordinary work. This may call the model and consume provider usage. It needs the
other admission gates first; `prepare` never performs this model smoke. Successful
qualification restores the owned initial workspace at the same paths and retains
its evidence. A JSON receipt alone cannot replace actual current isolation.

If the suite declares `host.credentials: "codex-native-auth"`, supply
`--auth-file /absolute/path/to/auth.json` for native preparation/run/compare.
The evaluator neither signs in nor copies/logs/hashes credential contents. It
mounts that external file read-only for native execution; candidate tools sharing
the container identity can read it. `outbound-enabled` permits broader outbound
traffic, not provider-domain-only access. Both exposures are explicit admission
choices. Runtime paths, versions and authentication must be qualified in the
actual prepared image before making native-support claims.

Suite commands reject explicit legacy overrides such as `--runner`, `--case`,
`--depth`, host/model flags, jobs, timeout, repeats and seeds. Put applicable
settings in the manifest. Suite comparisons are serial, with a fixed writer
budget of 900 seconds and 8 MiB per output stream. `check`, `review-template`,
`report`, `blind` and `apply-reviews` select their mode from saved records.
`check` without `--review` executes another assessment of the frozen delivery;
reporting, blinding and applying reviews execute no candidate/provider checks.

### Manifest reference

All listed fields are required unless explicitly marked optional. Nullable
fields still require a key with `null` when no value applies. IDs are opaque
lowercase identifiers. Resource paths are normalized relative paths under the
manifest directory; arbitrary absolute resource paths, escaping links, hard links,
duplicate JSON keys and unknown fields fail validation. Inventories bind file
bytes, modes and supported relative links. Keep oracle/qualification resources
outside every public task, product and command-resource exposure.

| Object | Fields / contract |
|---|---|
| Suite | `schema: "coding-trial-suite/v1"`, `id`, `purpose`, `tasks`, `arms`, `host`, `repeats`, `execution_seed`, `blind_seed`. |
| Task | `source`, `brief`, nullable `clarifications`, `scope`, `dependencies`, `checks`, `cleanup_required`, `local_commits` (`allow`/`forbid`), nullable `admission` receipt. |
| Source | `kind` (`fixture`/`git`), `url`, `commit`, `tree`, `export`, nullable `preparation`, `baseline_sha256`. Git sources bind complete upstream IDs and prepared export bytes. Fixture upstream IDs are null. |
| Scope | `implementation`, `tests`, `config`, `allowed_changes`, `protected`, `generated` path lists. Protected paths win. Generated/workflow exclusions cannot hide baseline or assessed code. |
| Dependencies | Nullable `environment` qualification, `locks`, `allow_lock_changes`. Allowed lock changes also require bound task/environment admission; a boolean alone is insufficient. |
| Checks | Disjoint `feature_ids`/`preservation_ids`, ordered `native` commands, private `oracle`, public `worker`. Each command has `id`, nullable `entry`, `support`, `argv`, `cwd`, `env`, `timeout_seconds`, `output_bytes`. Oracle/worker require an entry. |
| Arm | Nullable `product` for plain; `entry` prompt resource; `configuration` object; `workflow` (`none`/`forge-v1`/`unmeasured`); `artifacts`; `loading`. Product fields: `source_commit`, `source_tree`, `payload`, `inventory_sha256`. |
| Loading | `adapter` (`none`/`codex-plain-v1`/`codex-plugin-v1`), nullable `receipt`, optional nullable `selected_entry`. Scheduled native products require a nonnull payload-relative selected entry matching the installed file; plain/synthetic arms cannot select one. Omission and explicit null retain distinct input identities. |
| Host | `adapter` (`fixture`/`codex`), `executable`, `version`, `model`, `effort`, `isolation`, `capabilities`, nonsecret `environment`, `credentials`, `fixture_argv`. Fixture model/effort/credentials are null. Native executable paths refer to the image. |
| Isolation | Nullable object with `adapter: "docker-v1"`, `profile`, `image_digest`, nullable `probe_receipt`. The profile binds Docker executable/context/endpoint/server, image/platform, user, explicit limits, network, runtime paths and network checks. |
| Qualification | `schema: "coding-trial-qualification/v1"`, `kind`, `bindings`, `checks`, `producer`, `execution`, `evidence`. Hashes bind actual resources and exact declared policy; curator independence remains an attestation, not authenticated identity. |

The arm prompt `entry` and native `loading.selected_entry` serve different roles:
the first is the writer instruction; the second identifies an exact file inside
the frozen product, for example `skills/zagrosi-forge/SKILL.md`. Arm configuration
does not become arbitrary host CLI flags. Native products use the supported
`.codex-plugin/plugin.json` package format.

Commands are literal argv arrays. Only complete-token `{workspace}`, `{entry}`,
`{python}` substitutions apply to public checks; private oracle commands may also
use `{assessor}`, `{assessment}`, `{receipt}`. `cwd: "."` selects the appropriate
root. Check bounds are positive, at most 86,400 seconds / 8 MiB per stream; they
do not override the writer budget. The private Python oracle uses the frozen
controller with isolated interpreter settings and a bounded request/response
protocol for candidate observations.

Exact validation lives in the [manifest parser](../../../tools/coding_trial_manifest.py),
[qualification bindings](../../../tools/coding_trial_qualification.py) and
[native recipe](../../../tools/coding_trial_native.py). The
[synthetic fixture builder](../../../tests/trial_suite_fixtures.py) illustrates a
three-arm schema with non-src code and a declared dependency; its dummy checks
and null proofs are **not a qualified runnable study**. The
[comparison tests](../../../tests/test_trial_suite_comparison.py) document the
full logical workflow and explicitly identify execution doubles.

### Results and review authority

| Field | Meaning |
|---|---|
| `admission_gates` | Environment/task/isolation/loading setup status: passed, missing, unmeasured or failed. Successful preparation does not authorize a model launch. Missing native runtime/image prerequisites can still fail preparation. |
| `common_quality` | Passed/failed/pending native tests, oracle, scope, dependency and integrity checks **plus independent material review**. |
| `workflow` | Separate passed/failed/unmeasured/not_applicable result. Only `forge-v1` arms receive Forge workflow validation. |
| `acceptance` | Prospective acceptance requires a successful writer, common quality, required cleanup and current study eligibility. Synthetic accepted totals stay zero. |
| `synthetic_validation` | Offline outcome, separate from prospective acceptance. Missing measurements never become evidence of native qualification. |
| `study_complete` | All scheduled positions terminal and every produced candidate materially reviewed. A complete rejected study may still fail. Terminal no-output receipts need no fictitious code review; interrupted/unentered work remains incomplete. |

Exit0 means successful setup/artifact creation, or the purpose-specific accepted
outcome. Ordinary failed/pending run/check/compare/report/apply outcomes use exit1;
synthetic success requires all scheduled writers/checks/reviews to pass while
remaining study-ineligible. Syntax/preflight errors use exit2; suite failures
retain structured JSON error codes such as `loading-unqualified`, `input-drift`,
`receipt-invalid` and `review-stale`. Preparation may succeed with missing proof
reported explicitly. Raw events, failed attempts and numbered assessments remain.

Execution uses a seeded shuffled order rotated across blocks; blind ordering has
its own seed. Every scheduled attempt stays in the denominator. Total time
includes observed failed effort; unavailable usage/cost stays null. Partial stage
telemetry is retained separately, and zero accepted results leaves per-accepted
cost undefined. These observations do not establish general superiority.

Blind packets appear under `blind/000001/B000001/` with common task/baseline,
`C001/candidate`, public check summaries and `review.json`. Give reviewers those
blocks; retain private maps, workflow files, prompts, timing and raw oracle records
outside them. Every produced candidate needs readability, cohesion, duplication
and regression evidence, including unpreferred and failed candidates. Ties/no
preference are valid; preference cannot rescue behavior failure. An unchanged
cleanup-required candidate can receive an honest failing review with
`meaningful:false` and no changed files; it cannot pass cleanup.

Keep each **original comparative review form** after application. Reports verify
its bytes, mapping, code and current behavior receipts; deleting/changing a form
invalidates effective comparison review even if a copied per-attempt review
still passes. A fresh behavior check requires fresh blind/review evidence.
Numbered generations/applications preserve history, including failed application
attempts. Blinding is partial because code itself can reveal origin.

## Legacy fixtures and comparisons

The remaining sections describe the original case registry, host adapters and
saved formats. Commands without `--suite` keep those contracts and defaults.

Isolated cases cover an ordinary summary feature, behavior-preserving cleanup,
deep discount design, a real Forge resume checkpoint, an order-dispatch godfile,
an import preview across several modules, a Node summary feature, and TypeScript
permission/transaction cleanup, and webhook retry correctness. The invoice cases preserve public APIs, exact exports, rounding, and
errors. The godfile case exercises cohesive module extraction while preserving
validation, pricing, shipping, serialization, file access, and order transitions.
The ordinary summary feature request does not prescribe a refactor. Fresh writer
and reviewer prompts disclose required cleanup equally to all arms; independent
review judges useful changes to encountered code. Historical prompts remain unchanged. Its unrelated
`src/legacy_reports.py` is protected; editing it fails scope even if tests pass.
The Node case uses CommonJS, `node --test`, and an independent Node oracle without
a framework. Node must be available to run it.

`config-layers`, `atomic-publish`, `stream-records` and `node-events` add caller
contracts for configuration provenance, rollback, incremental decoding and event
subscriptions. Their ordinary fixtures, independent oracles, good implementations
and deliberate faulty controls remain separate; controls never enter writer workspaces.

Fresh `atomic-publish` evaluations check full observed existing permission bits,
baseline-valid long filenames and cleanup after staging write or metadata failure.
Fault probes accept buffered and descriptor-based implementations, including import
aliases. The oracle reports exercised routes and special-bit support on stderr;
unexercised routes are coverage limits. New-file modes remain unspecified because
the original prompt and good control are ambiguous on that point. These stronger
checks do not regrade frozen trials or establish improved model output.

`import-preview` adds a CSV batch preview across import, pricing and receipt modules.
Its two initial tests cover only happy paths; the independent oracle also checks
quoted/Unicode fields, empty batches, duplicate normalized rows, unknown SKUs,
invalid quantities/prices, exact export/error compatibility and input preservation.
The ordinary feature request does not prescribe cleanup. Independent review must
confirm useful incidental cleanup of encountered duplication; unrelated
`src/customer_reports.py` is protected. This case can run at every depth.

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
Fresh workspaces require Git and receive their own committed fixture baseline,
so change inspection works even inside an ignored parent directory. Preparation
isolates Git configuration, hooks, templates and signing; the surrounding
repository remains untouched. Planning and generated caches are excluded through
local Git metadata. Historical workspaces retain their original layout.
Trusted evaluator case definitions may set `test_argv`, a nonempty array of
nonempty strings without NUL bytes, to select their native test command. The
array executes directly without shell expansion. Invalid definitions fail before
workspace creation; candidate files cannot select or replace this command.
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
The Python invoice oracle also compares malformed-input errors, input access and
one-shot iteration, public imports/signatures/defaults, and ignored options with
the baseline. Known regressions fail while valid shared-calculation controls pass.

### Verdicts and cleanup review

Results keep these decisions separate:

- `behavior`: the independent oracle completed and existing/added tests passed.
- `workflow`: Forge admits the unique plan at or beneath `.planning` with strict checks at the
  selected trial depth, then verifies implementation completion records. A narrative
  "done" note is insufficient; competing planning roots and escaping links fail.
- `cleanup`: for cases marked `cleanup_required` in the [case registry](cases.json), implementation code must change, and an
  independent reviewer must confirm useful cleanup with concrete changes and
  before/after regression evidence. Existing tests and the independent oracle can
  supply sufficient coverage; add tests where coverage is weak. Test changes are
  reported separately. Other cases report `not_required`.
- `common_quality.success`: behavior, allowed scope/dependencies, unchanged
  evaluator and applicable plugin provenance, required cleanup review, and runner
  checks, applied equally to every arm. Forge workflow and resume-history evidence
  are excluded from this code-quality measure.
- `success`: the existing delivery verdict, which additionally requires Forge
  workflow completion and preserved resume evidence where applicable. Those
  delivery requirements are unchanged.

An unchanged cleanup fixture can pass behavior, but cannot pass the overall trial.
Comments, formatting, docstrings and empty new modules do not establish useful cleanup.
Python changes are compared using syntax trees; TypeScript/JavaScript changes use
file hashes. The independent review determines whether changes are substantive.
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

### Provenance and measurements

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

### Repeated complete tasks

`tools/trial_matrix.py` runs fresh workspaces for each case/depth/repetition.
Use an immutable plugin checkout and the same agent configuration for comparisons.
The runner reads each prompt from stdin and starts in the candidate workspace.

```bash
python tools/trial_matrix.py run /tmp/forge-matrix --plugin-root /path/to/snapshot \
  --cases summary cleanup resume --depths lean standard deep --repeats 2 \
  --jobs 2 --runner /path/to/agent-runner
python tools/trial_matrix.py report /tmp/forge-matrix
```

Every scheduled attempt remains in the denominator, including failures, timeouts
and pending work. A passing checker without a completed runner is insufficient.
Groups retain delivery `outcomes` and `accepted_outcomes`, and add
`common_quality_outcomes` and `accepted_common_quality`. Each attempt records
common quality as `passed`, `failed`, or `unknown`; old results without the new
field remain unknown and are never regraded from delivery success. Both cost
aggregates include all scheduled attempts. Their acceptance rate is recorded
acceptances divided by scheduled work, not a pass rate among checked candidates;
read it alongside the explicit unknown count. Missing runner or attempt evidence
cannot establish common acceptance. A nonzero checker exit caused solely by
missing Forge workflow can coexist with accepted common code quality.
Each trial retains its log, runner time, preparation/check time, behavior and
workflow verdicts, code metrics and source fingerprints. Supply independent
cleanup reviews with the existing `check --review` command; rerun checks after
source/review edits, then regenerate the matrix report. Optional per-trial
`telemetry.json` is labeled runner-reported; unavailable usage/retry counts remain
unknown. Timing summaries include unsuccessful timed attempts. External review
time is excluded from runner time. Two repetitions give observations, not a
statistically established speedup.

Scope permits `.gitignore` entries only for local planning and generated Python/pytest caches; ignoring source or tests fails the trial.

See the [2026-09-24 observations](results-2026-09-24.md) for repeated tasks across
all three depths, including failed and cancelled attempts and comparison limits.

### Controlled Forge/plain comparison

[24 September controlled pilot](controlled-results-2026-09-24.md): lower observed input use, incomplete Forge outcomes, and independent review failures.

The historical measurements use Codex CLI and do not cover Claude Code. The
legacy adapter also supports Claude; new runs must report their own outcomes.
Other agents can use the explicit custom runner contract above.

Use **one frozen evaluator checkout** for the entire experiment. `--plugin-root`
selects only Forge's source under test; its evaluator, cases and fixtures are never
executed. The fixed evaluator's Forge runtime judges every Forge arm by the same
admission/completion rules. Plain agents receive the same functional task, scope,
regression checks and independent cleanup review; Forge records are inapplicable.
Task fixtures contain no arm-specific workflow instruction. Forge's prepared
`resume` checkpoint is excluded from this comparison because it has no equivalent
plain-agent starting state.

The default comparative cases are the 251-line order-dispatch module with state,
serialization and extraction requirements, and the multi-module CSV import preview
with weak initial coverage. No artificial padding or arbitrary line-count gate is
used. Each case/depth/repetition runs current Forge and plain agent in rotating order.
Add `--previous-root` for a third, previous-Forge arm only when its saved evidence
contract remains compatible with the fixed evaluator. Comparisons run serially
to avoid concurrent workload bias.
All arms use the checked-in adapter, the same explicit model/effort, a fresh
workspace/session, ignored user config/rules and identical sandbox settings.
Confirm the chosen model works with the selected host binary before scheduling.

```bash
# Run this script from the frozen evaluator, which includes the new harness.
python /path/to/evaluator/tools/trial_matrix.py compare /tmp/forge-controlled \
  --plugin-root /path/to/current \
  --model gpt-5.5 --effort medium --codex /opt/homebrew/bin/codex \
  --cases godfile typescript-access --depths standard --repeats 1 --timeout 600
# Four fresh attempts; cleanup results remain pending independent review.
python /path/to/evaluator/tools/trial_matrix.py blind /tmp/forge-controlled
```

Give an independent reviewer each directory under `blind/`, **without**
`blind-key.json`, parent logs, arm names, timing or usage. Packets contain original
source/tests, original task requirements, shuffled labeled candidates, behavior/scope check results and
`review.json`. Reviewers explain readability, cohesive boundaries, removed
redundancy and regression protection using concrete files/functions. They select
preferred labels (ties allowed), explain the choice, and complete the ordinary
cleanup evidence for each candidate. Code may reveal workflow fingerprints, so
blinding is partial; there is no automatic quality score from line counts.

```bash
# After independent reviewers complete every blind/*/review.json:
python /path/to/evaluator/tools/trial_matrix.py apply-reviews /tmp/forge-controlled
python /path/to/evaluator/tools/trial_matrix.py report /tmp/forge-controlled
```

Applying reviews validates baseline/candidate hashes against both the original
workspace and review packet, checks complete boolean evidence against the
source-bound result, writes the existing per-trial cleanup evidence,
rechecks candidates with the fixed evaluator, and retains comparative judgments
in `summary.json`. Source/test changes invalidate review; stale checker output
cannot create a blind packet. Templates grant no passing verdict.
Preference is distinct from acceptance: a preferred candidate with passing
behavior/scope checks can still fail required cleanup. Every judgment stays visible.

The adapter saves raw `agent-events.jsonl` plus atomic partial `telemetry.json`,
including CLI version, requested model/effort, total input/output tokens and
cached/uncached input tokens where reported. Missing fields remain unknown.
`commands_by_phase` labels observed planning, implementation, verification and
other commands with counts, failed commands, event-receipt durations and emitted
output bytes. These are **not** exact phase wall time or phase token usage;
commands may combine phases, overlap, or have truncated output. Identical commands
repeated after failure are observed command retries; API retry counts remain
unknown. Requested model names do not attest the backend revision. Ignore-rules
and the plain prompt reduce workflow contamination, but do not prove the agent
has never learned or encountered Forge. Two repetitions are a controlled pilot,
not statistical evidence of improvement across all depths or tasks.

A controlled report is complete only after every scheduled comparison block has
a valid independent review, even if ordinary trial checks already pass. Missing
or deleted reviews remain in the expected denominator. A failed runner with
checked output can still be reviewed; an attempt without `result.json` or source
cannot be ranked. Packet preparation checks every attempt before creating
`blind/`. Preserve those failures, run the fixed `check` for existing workspaces,
and then retry packet creation. If preparation never produced a workspace,
retain the incomplete block and schedule any replacement as a new attempt;
never erase the original failure. An empty preferred-label list is allowed when
no candidate is acceptable.

### Native hosts and accepted outcomes

The built-in writer adapter supports Codex and Claude Code. Compare Forge and
plain work **within the same host, requested model and effort**; separate host
experiments do not isolate a plugin effect. For example:

```bash
python3 tools/trial_matrix.py compare /tmp/forge-claude-comparison \
  --previous-root /absolute/path/to/previous-forge --host claude \
  --model YOUR_CLAUDE_MODEL --effort high --cases typescript-access --repeats 2
```

The TypeScript fixture requires Node24 and the pinned tooling-only compiler
(`npm ci --prefix tools` in the evaluator checkout). It exercises duplicate authorization
policy across modules, public exports, validation order, tenant isolation,
auditing and rollback through 394 independent assertions. Known-defect tests
check that the oracle rejects authorization, transaction, ordering and export
regressions. An immutable external consumer checks exported types and function signatures
before the runtime oracle imports candidate code. Missing or mismatched compiler,
type erasure and incompatible signatures fail acceptance; candidate tests and
compiler configuration cannot disable this check. The compiler emits no files. Source-byte changes alone do not prove useful TypeScript cleanup;
independent review must explain the concrete improvement.

Native writer runs use existing CLI authentication and consume the account's
usage. They do not read or copy credentials. Claude loads the selected plugin
with `--plugin-dir`, suppresses user/project settings, and preserves OAuth; its
plain arm omits the plugin. Host permission controls differ. The disposable trial
workspace and scope checker are evaluation boundaries, not a security sandbox.

Reports retain every attempt and add accepted rate, elapsed time, cached/uncached
input and output usage, interventions and CLI-reported cost per accepted result. Failures count
in totals. Missing observations stay unknown; zero accepted results leaves
per-accepted figures undefined. Claude final-result usage is counted once;
reported costs are estimates, not subscription charges. Independent review time
is currently unmeasured. Offline stream tests are parser evidence, not live
model-quality results. Historical reports above remain unchanged.

### Deliberate interruption and a fresh session

Use the existing `resume` case and supply the second runner as a JSON argv
array. The harness saves both process attempts, transcripts and telemetry,
confirms timeout cleanup, then starts a fresh process in the same workspace.
The second runner may select the other host. The original red checkpoint and
regression test must survive and the completed behavior must pass the oracle.

```bash
python3 tools/coding_trials.py run /tmp/forge-cross-host --case resume \
  --interrupt-after 30 --timeout 600 \
  --resume-runner '["python3","/absolute/path/to/forge/tools/coding_trial_runner.py","--host","claude","--model","YOUR_CLAUDE_MODEL","--effort","high"]' \
  --runner python3 /absolute/path/to/forge/tools/coding_trial_runner.py \
  --host codex --model YOUR_CODEX_MODEL --effort high
```

If the first process finishes before interruption or cleanup is unconfirmed,
the interruption trial fails explicitly. A successful helper-process recovery
test does not claim that either model used a skill correctly.

### Native skill acceptance matrix

Use a frozen evaluator checkout and plugin snapshot. This separate harness uses
the existing first-task example: twelve host/depth/directness workflows, each
with planning-only, an implementation follow-up interrupted at a new red
checkpoint, and fresh continuation; plus one unsupported request per host.
It independently checks the original failing test, unchanged requirements/tests,
saved input hashes, final behavior, admitted records and captured verification.
These are workflow checks, not performance or general code-quality comparisons.

Native `prepare` copies the selected package into a UUID namespace under the
active Codex plugin cache (`$CODEX_HOME/plugins/cache`, default
`~/.codex/plugins/cache`). It retains that namespace for later continuation;
remove only the recorded namespace after finishing. Use the isolated coding-trial
harness above when writing to the native cache is outside the authorized scope.

```bash
# No model calls: native staging/discovery, fixed inputs and evaluator hashes.
python /frozen/evaluator/tools/native_workflow_trials.py prepare /saved/native-matrix \
  --plugin-root /frozen/plugin --codex-model YOUR_CODEX_MODEL --effort medium
# Explicit model-call opt-in. Prove one row before starting the host batch.
python /frozen/evaluator/tools/native_workflow_trials.py run /saved/native-matrix \
  --case codex-lean-indirect --resume-host codex
python /frozen/evaluator/tools/native_workflow_trials.py run /saved/native-matrix \
  --host codex --resume-host codex
python /frozen/evaluator/tools/native_workflow_trials.py report /saved/native-matrix
```

Calls and whole rows have separate deadlines. Each attempt retains its prompt,
native events, CLI version, result and reported usage. A host batch stops on a
failure; failed attempts are never overwritten. Diagnose shared defects before
preparing a new matrix. Missing authentication is blocked, not passed or failed.
Codex inherits native settings unchanged and uses matching per-process plugin
overrides for discovery/execution. Claude uses session-only `--plugin-dir`.
Authentication stays with each CLI; the harness never copies credential stores.

`--resume-host codex` permits Codex-only acceptance while preserving direct rows'
cross-host checks as pending. Keep the matrix, frozen evaluator/plugin trees and
the unique `installation.temporary_cache` directory recorded in `matrix.json`.
The harness saves each exact interrupted workspace before continuation. After
native Claude sign-in, use the same frozen evaluator and an explicit model:

```bash
python /frozen/evaluator/tools/native_workflow_trials.py resume /saved/native-matrix \
  --case codex-lean-direct --claude-model YOUR_CLAUDE_MODEL
python /frozen/evaluator/tools/native_workflow_trials.py run /saved/native-matrix \
  --host claude --claude-model YOUR_CLAUDE_MODEL
```

Repeat `resume` for the standard/deep direct rows. It restores the verified red
checkpoint at its original path and archives the prior completed candidate.
After all desired continuations, only the recorded UUID cache namespace is
disposable; user settings and other installed plugin directories remain separate.

### Retry bug-fix fixture

`retry-queue` covers synchronous webhook delivery with tenant-scoped completion,
failed sends, repeated events, full-batch validation, audit order and nested
payload mutation. Its 271 independent assertions run outside the editable
workspace. The original bug and five independently broken variants fail the
oracle; a valid fix passes. This is an in-memory journal fixture, not a claim of
exactly-once delivery across crashes or concurrent workers.

### Native package identity

New matrices use versioned, path-independent hashes for every declared package
member plus the native loading roots. Missing files, unsafe paths, or additional
loading metadata invalidate the run before model execution; source and staged
copies must agree. Reports identify legacy coverage explicitly. Keep historical
matrices with their original frozen evaluator; the new evaluator rejects legacy
continuation instead of rewriting their evidence. Compiler configuration and the
external type consumer are also included in coding-evaluator identity.
