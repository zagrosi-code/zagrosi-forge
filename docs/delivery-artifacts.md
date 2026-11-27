# Verify a release artifact

Record the artifact for a tracked PR when the build produces it. The receipt identifies the PR, Git revision, release stage, environment, operator and source. Use a source that identifies the exact build, object version or registry record.

```sh
python3 scripts/zagrosi_skills.py delivery artifact record \
  --pr 42 --sha "$HEAD_SHA" --stage build --environment review \
  --name runtime --file dist/runtime.zip --source builds/42 --actor builder > runtime-receipt.json
```

Forge hashes the file as a stream and records its basename, byte count and SHA-256. The Git revision must be the current PR head or its recorded landing commit. Each distinct build can have its own receipt, including multiple builds from the same revision. Repeating the same record with the same inputs preserves its original timestamp and ID. Supply `--observed-at` with an explicit timezone to enter a dated observation.

Give the receiver the artifact and the JSON receipt from a trusted source. The receiver can run the installed launcher's verification command from any directory, including a machine without the producer's Git checkout.

```sh
python3 /path/to/forge/scripts/zagrosi_skills.py delivery artifact verify \
  --receipt runtime-receipt.json --file downloads/candidate.zip
```

A byte match returns exit zero, `matched: true` and `verification: file_bytes`. A mismatch returns exit one with the observed byte count and digest. The receiver can rename the file. The receipt ID helps detect changes in transit. The producer's identity and the association of the bytes with the commit come from the cited source.

## Container images

Use a digest from the registry or build record. Preserve the repository name when it matters for the promotion.

```sh
python3 scripts/zagrosi_skills.py delivery artifact record \
  --pr 42 --sha "$LANDING_SHA" --stage deploy --environment production \
  --name payments --image "registry.example/payments@$PUBLISHED_DIGEST" \
  --actor release-job --source promotions/42 > image-receipt.json
python3 scripts/zagrosi_skills.py delivery artifact verify \
  --receipt image-receipt.json --image "registry.example/payments@$DEPLOYED_DIGEST"
```

`PUBLISHED_DIGEST` and `DEPLOYED_DIGEST` each contain `sha256:` and 64 lowercase hexadecimal digits. For a qualified receipt, both the supplied image name and digest must match. A bare digest receipt checks just the digest. A match reports `verification: reported_reference`. Both references come from the operator, and comparison runs offline. A deployment or customer sign-off still needs its own [delivery observation](delivery.md).

## Find a receipt

`delivery status --pr 42 --environment production` includes each recorded artifact for that environment. Omit the environment to list all artifact receipts. A current receipt matches the current head or exact landing SHA. If two builds at that SHA have different bytes, both receipts can be current. Select the digest and source for the particular build you intend to use. A receipt from an earlier revision appears as stale. Linked worktrees share the ledger, and independent clones can use the portable JSON for verification.
