# Verify a pull request’s sections

A section group lets one pull request cover several sections of a larger shared plan. It reserves their combined write paths and verifies a particular Git commit. Other lanes can continue editing the remaining sections.

## Reserve the group

Prepare the committed plan as described in [team collaboration](collaboration.md#bind-a-forge-plan). Use the private planning directory returned by `team prepare` to start a task.

```bash
python3 scripts/zagrosi_skills.py team start --target-dir . --task "Build the outbox" \
  --planning-dir "$prepared" --sections section-02-queue section-03-worker
```

Use `team update` or `team check` with the same selectors, session and generation to change or check the reservation. An update replaces the group’s previous scope in one board operation. Each member can run ordinary section verification and recording. Group integration uses the snapshot command below. Whole-plan postflight still requires its own full-plan claim. Preparing a committed plan lets all lanes read its canonical files while each reserves only its own implementation files.

## Hand off the committed tree

Commit the code and the shared contract. From the source checkout, export the group’s identity. The planning directory here can be either the canonical committed plan or its private prepared copy.

```bash
python3 scripts/zagrosi_skills.py section-group create --target-dir . \
  --planning-dir docs/plans/outbox --pr example/app#42 --sections section-02-queue section-03-worker > outbox-group.json
```

Give the JSON and commit SHA to the receiving engineer. In a second checkout at that commit, inspect the handoff and rerun a locally chosen command.

```bash
python3 scripts/zagrosi_skills.py section-group inspect --target-dir . --handoff outbox-group.json
python3 scripts/zagrosi_skills.py section-group verify --target-dir . --handoff outbox-group.json \
  -- python3 -m pytest -q tests/outbox
```

Forge derives the commit, Git tree, plan digest, selected sections and owned paths from the receiving repository. A different commit, edited contract or altered identity fails the check. For a package inside a monorepo, use that package as the target on both hosts.

Verification starts in a temporary checkout of the exact commit, in the target directory. Pass a directory-qualified command such as `./tools/check` or `.\tools\check.exe` to run an executable from this checkout. Install any dependencies the check needs there or invoke a suitable local interpreter. The checked-out commit excludes local source edits, untracked inputs and other worktrees’ progress. A failing command, timeout or change to the verification checkout fails the result. Commands may create ignored build outputs. The bounded command output, duration, unique run ID and actual exit code identify the locally observed run. Only the locally supplied command after `--` runs. A handoff creates no local section-completion record.

The optional `--pr` label identifies a pull request in a report. A handoff carries historical recorded, pending or unknown status for each selected section. Recorded outcomes include a record digest and its original commit and tree when available. After verification, `history.group_check` reports the run ID, command digest, duration, exit code and outcome. Inspection displays these sender-reported summaries separately from any fresh execution. To share a copy without the local command output, save the result of `section-group inspect`.

Use `section-group verify --omit-output` before the `--` separator to produce an importable receipt without stdout or stderr. The receipt retains the exact command arguments, outcome, timing, run ID and Git identities. Review command arguments before sharing a receipt. Both `delivery test --group-report` and `build-report --group` accept the resulting JSON.
