# Required delivery checks and sign-offs

A tracked PR can declare CI jobs and name the acceptance decisions needed in each environment. Run `delivery require` with the full list of names for the scope you are changing. The examples use PR 42 and the repository launcher from the checkout root.

```sh
python3 scripts/zagrosi_skills.py delivery require --pr 42 --stage ci --name server --name migration
python3 scripts/zagrosi_skills.py delivery require --pr 42 --stage acceptance --environment uat --name Finance --name IAM
```

Record each result under its declared, case-sensitive name. The CI example records a PR head check. For target CI after merge, provide the landing commit and target ref as described in [delivery evidence](delivery.md).

```sh
python3 scripts/zagrosi_skills.py delivery observe --pr 42 --stage ci --name server --outcome passed \
  --sha "$HEAD_SHA" --base-sha "$BASE_SHA" --ref refs/remotes/origin/feature --actor ci --source "$JOB_URL"
python3 scripts/zagrosi_skills.py delivery observe --pr 42 --stage acceptance --environment uat \
  --name Finance --outcome accepted --sha "$TESTED_SHA" --actor "Finance lead" --source "$DECISION_URL"
python3 scripts/zagrosi_skills.py delivery status --pr 42 --environment uat
```

The status row includes `stages.ci.requirements` and `stages.acceptance.requirements`. In this example, server passed and Finance accepted. Migration and IAM are missing, so both stages report `missing`. Each observed name retains its actor, source, time and commit. An unnamed workflow result remains available in history. Each individual sign-off records the operator’s attribution of the decision to its source.

Forge selects each name’s most recent observation applicable to the current revision and environment. Other-revision evidence appears as `stale` with its previous outcome. A PR check on a fast-forwarded commit becomes stale after merge until CI checks the landing on the target ref. The overall stage gives failures and rejections first, followed by stale, missing, pending and skipped requirements. Passing CI or accepted sign-off requires every declared name to succeed.

Replace the list to change the required names. Use `delivery require --pr 42 --stage ci --clear` to restore the existing unnamed CI summary. For acceptance, include `--environment` to clear only that environment. The ledger keeps earlier named and unnamed observations under `delivery status --history`; declaring the same name again makes its applicable evidence visible.
