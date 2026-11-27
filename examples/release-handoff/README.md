# Review a release handoff

From the Forge checkout or installed plugin directory, run the example with Python 3.11+ and Git. It creates a temporary repository and a linked reviewer worktree. All checks run locally.

```sh
python3 examples/release-handoff/run.py
```

To inspect the results, supply a **new** directory. On Windows use `py -3` in place of `python3`.

```sh
python3 examples/release-handoff/run.py --keep my-handoff
```

The parent PR creates an invoice table. The child adds an approval column. A real SQLite check confirms the original invoice keeps its amount and defaults to unapproved.

| Open | What to look for |
|---|---|
| `initial.md` | The schema check passed. The required migration check is missing. |
| `initial-build/artifact.json` | The ZIP has a recorded digest and size. The reviewer accepts a byte-identical copy and rejects a different ZIP with the same filename. |
| `stale.md` | A parent change invalidated the child's checks. The report identifies the sections and previous tested revision. |
| `review.md` | The child was rebased, both checks reran and the new artifact is recorded at the rebased commit. |
| `final-receipt.json` | The importable run includes the exact command, commit, timing and outcome. Stdout and stderr were omitted. |

To check the received bytes again from the Forge directory:

```sh
python3 scripts/zagrosi_skills.py delivery artifact verify \
  --receipt my-handoff/rebased-build/artifact.json \
  --file my-handoff/rebased-build/received/invoice-migration.zip
```

The example’s `reviewer` worktree shares the delivery ledger with the author. Run `delivery status --target-dir my-handoff/reviewer --pr 42 --environment review --history` with the same launcher to inspect the original and rebased observations.
