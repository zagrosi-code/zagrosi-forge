# Build measurements

Use `build-report` to see what Forge measured while a plan was being implemented. It reads the selected plan and any observations you explicitly supply. It does not run a check, contact a provider, or inspect your chat history.

```sh
python3 scripts/zagrosi_skills.py build-report --planning-dir plans/import
python3 scripts/zagrosi_skills.py build-report --planning-dir plans/import --section section-01-parse
python3 scripts/zagrosi_skills.py build-report --planning-dir plans/import --group pr-42-verified.json
```

Pass `--group` again for more groups in the same plan. The committed plan digest must match and the checkout must contain the recorded commit and tree. The report adds a row for each named pull request. Section receipts describe work on those sections across the retained local plan history. The separate group verification row describes the recorded check against the handoff's commit and tree. Sections that belong to more than one PR appear under each PR. The summary counts each observed execution once. A group check covers the group as a whole and contributes to the PR total. Looking at one member of a larger group excludes that group-wide check. The report accepts group history as context and uses only the fresh verification object to count a group execution.

## Checks and time

Each captured command gets a unique execution ID. A command captured once for both a section and integration counts once in the summary and appears under both scopes. Running the same command twice produces two IDs. `repeated_checks` counts invocations after the first for each exact argument list, and `repeated_commands` identifies those commands. Failed and timed-out checks count as work. The exit code and any group snapshot failure determine their outcome.

`check_seconds` adds command durations. `check_wall_span_seconds` measures time between the earliest recorded start and the latest finish, including gaps between checks. Parallel checks may overlap. These intervals cover recorded checks only. Missing duration or clock data leaves the corresponding total unknown.

Ordinary capture history, compatibility pairs and their prior attempts, and state-first checks all contribute. State-first writes a small measurement record alongside its local verification receipts so a retry survives replacement of the visible status. A manual attestation or reported command has no measured execution. An interrupted capture without an execution ID, a legacy receipt, or a section with no retained measurement leaves the relevant total `null`. Fields beginning with `observed_` give the amount established by the supplied evidence. Deleting local records can reduce this amount.

## Attribute reviews and context

Pass the section name and file for each review or usage observation. Use `integration` for work spanning the plan. A dedicated review file contributes both review and usage observations. Only attribute a review session containing the review you intend to count.

```sh
python3 scripts/zagrosi_skills.py build-report --planning-dir plans/import \
  --usage section-01-parse writer.jsonl \
  --review section-01-parse provider-review.json
```

Supported files include Forge's `provider-review` JSON, native Codex execution JSONL with a thread ID and turn results, Codex rollout JSONL with session metadata, cumulative token usage and task completion, and Claude JSON or stream JSONL with a session ID. Claude result totals use per-model usage when available, including cache reads and writes. Codex input tokens already include cached inputs. Cached inputs are a subset of `input_tokens`. Aggregate context usage expresses tokens processed across requests, not peak context-window occupancy.

Repeated copies of a native session, cumulative checkpoints, or streaming updates to one Claude message count once. A provider review with a native session ID can be paired with its original transcript. Contradictory session totals, interrupted runs, missing provider fields or missing section attribution leave complete totals unknown. The same session cannot be charged to two sections. Older Forge review files without a unique execution ID can establish an observed review, while their exact round count stays unknown. The report never includes conversation text or provider output.
