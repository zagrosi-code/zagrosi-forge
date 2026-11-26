# PR stacks and delivery evidence

Use Forge's `delivery` commands when work spans several PRs or you need to report what reached a particular environment. They read local Git refs and objects. Evidence lives in `forge-delivery.json` in the Git common directory, so linked worktrees share it. Separate clones have separate ledgers. Fetch through your usual Git workflow before assessing remote-tracking refs. The commands never fetch, switch branches, change refs, open PRs or publish evidence.

The examples use the Forge repository's launcher. When using an installed plugin, substitute its absolute launcher path. Pass `--target-dir` to select the repository from another directory.

## Track a stack

Register a parent before its children. Supply the full local or remote-tracking refs that correspond to the PR head and target. Use `--expected-base` to check the base against a full commit SHA from the PR. By default Forge records the locally observed base SHA.

```sh
python3 scripts/zagrosi_skills.py delivery track --pr 71 \
  --head-ref refs/remotes/origin/products --base-ref refs/remotes/origin/main
python3 scripts/zagrosi_skills.py delivery track --pr 72 --parent 71 \
  --head-ref refs/remotes/origin/warehouse --base-ref refs/remotes/origin/products
```

Record the commit actually tested and where the result can be checked. A passing result applies to the PR head when it contains the current base, or to a two-parent test merge whose immediate parents are that exact head and base. Forge also checks inclusion of the effective upstream commits. After merge, a new test runs against the exact landing commit. Use the full tested commit SHA from CI, which must be available locally. With `--checkout`, Forge reads HEAD from the specified clean worktree. Both forms record an attestation of the cited test run.

```sh
python3 scripts/zagrosi_skills.py delivery test --pr 72 --tested-sha "$TESTED_SHA" \
  --source "$CHECK_URL" --actor ci
python3 scripts/zagrosi_skills.py delivery status --pr 72 --history
```

The response shows the current head and tree, the stored expected base, the observed base, the upstream chain and the last tested commit and tree. `verification.status` becomes `recheck` when the basis for the test changes. The `changed` fields and the old test snapshot identify the difference. An unrelated PR retains its own evidence. Failed or skipped tests can be recorded with `--outcome`. A later passing attestation on the new basis makes the check current again.

After a squash merge, record the actual landing SHA and the original PR head. Retarget the child to its new base with `track`, keeping `--parent` for the dependency. Check out or fetch the rebased child, rerun its check and record its tested SHA. Tracking updates preserve previous tests and observations. Use `status` without `--pr` to see all affected descendants, including from a second worktree.

## Hand over a checked group

Create the section group with the same PR identifier used in the delivery ledger. Save the JSON returned by `section-group verify`. A colleague in another linked worktree can import the completed run directly.

```sh
python3 scripts/zagrosi_skills.py delivery test --pr 72 \
  --group-report /tmp/warehouse-check.json --source "$CHECK_URL" --actor ci
python3 scripts/zagrosi_skills.py delivery status --pr 72
```

Each entry under `verification.checks` identifies the sections, command, run, commit, tree, and upstream revisions it covered. The importer checks the report against the committed plan and preserves the outcome as reported evidence. Group results are ordered by the producer’s finish time. For older reports without execution timestamps, supply `--observed-at` or use the import time. Re-importing a run keeps its original evidence and freshness. After restacking, run the stale checks again and import their new reports. Use `--check NAME` on a manually recorded test to distinguish several checks on the same PR.

## Record what happened

Record each outcome separately. `--actor` names the person or system that produced the evidence and `--source` identifies the job, review, operator record or customer message. The timestamp defaults to now. Use `--observed-at` with a timezone when entering an earlier event.

```sh
python3 scripts/zagrosi_skills.py delivery observe --pr 71 --stage review --outcome approved \
  --sha "$PR_HEAD" --base-sha "$REVIEW_BASE" --actor reviewer --source "$REVIEW_URL"
python3 scripts/zagrosi_skills.py delivery observe --pr 71 --stage merge --outcome merged \
  --sha "$MERGED_SHA" --head-sha "$PR_HEAD" --actor maintainer --source "$PR_URL"
python3 scripts/zagrosi_skills.py delivery observe --pr 71 --stage ci --outcome passed \
  --sha "$MERGED_SHA" --ref refs/remotes/origin/main --actor ci --source "$WORKFLOW_URL"
python3 scripts/zagrosi_skills.py delivery observe --pr 71 --stage deploy --outcome skipped \
  --sha "$MERGED_SHA" --environment production --actor ci --source "$DEPLOY_JOB_URL"
python3 scripts/zagrosi_skills.py delivery status --pr 71 --environment production
```

| Stage | Outcomes | Required identity |
|---|---|---|
| `review` | `approved`, `changes_requested`, `dismissed`, `pending` | Reviewed PR head and its exact base (`--base-sha`) |
| `merge` | `merged`, `open`, `closed` | Observed SHA and original PR head (`--head-sha`) |
| `ci` | `passed`, `failed`, `skipped`, `pending` | Checked SHA and the full PR head or target `--ref`. PR CI also takes the tested `--base-sha`. After merge, report CI for the exact landing SHA on the target ref. |
| `deploy` | `passed`, `failed`, `skipped`, `pending` | SHA and named environment |
| `live` | `passed`, `failed`, `skipped`, `pending` | SHA and named environment |
| `acceptance` | `accepted`, `rejected`, `skipped`, `pending` | SHA and named environment |

For CI on a synthetic merge, provide its SHA and the exact head ref and base SHA for the PR leg. Forge checks its immediate parents. A fast-forward merge can share a SHA with the final PR check. Only CI explicitly observed on the target ref counts as target CI.

For a merged PR, refresh the local tracked target ref so its history contains the landing SHA. A later commit at the target tip is fine. If you supply the pre-merge `--base-sha`, it must be an ancestor of the landing. Supply it for a fast-forward when the target moved after Forge last tracked or tested the PR. Use the original head SHA from the hosted PR record, including for a squash merge or after someone deletes or reuses the source branch. Replaying the same landing and source head preserves the recorded base when you omit `--base-sha`.

Use `--environment` in status to select deployment, live and customer observations. Status lists available environment names. A green workflow and a skipped production deploy show `ci: passed` and `deploy: skipped`; absent customer evidence shows `acceptance: unknown`. Mismatched evidence is labelled `stale` and retains its previous outcome and source. Historical records remain available with `--history`.

Sources and actors are supplied by the operator. Forge checks local commit identities, ancestry and evidence scope. It does not query or authenticate external systems. Refresh evidence after a restack, deployment, reviewer dismissal or customer decision.

Use `delivery status --pr 42 --environment uat --format markdown` to generate a PR handoff for a reviewer. It lists the sections and exact commits tested, delivery stages, named requirements, artifact identities and next actions. The Markdown omits command arguments, process output and local evidence paths. The default JSON includes the structured evidence used for investigation and reruns.

Add `--history` to the Markdown command to include a chronological record of tests and stage observations across all environments. Each entry gives the time, revision and original outcome. The delivery summary above it shows which evidence applies to the selected environment and current revision.

See [required CI checks and sign-offs](required-checks.md), [artifact verification](delivery-artifacts.md) and the [runnable handoff](../examples/release-handoff/README.md).
