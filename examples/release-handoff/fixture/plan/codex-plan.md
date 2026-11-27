<!-- FORGE_META
{"artifact_type":"compact_plan","depth_mode":"lean","source":"spec.md"}
END_FORGE_META -->
# Invoice migration

## Owned files
- schema.sql
- migration.sql
- check.py

## Tests first
Case: Insert an invoice, then migrate the populated table.
Expected: After the migration the amount is still 950 cents and the approval flag is zero.
Command: python check.py migration

The earlier schema smoke check runs `python check.py schema` and reads the invoice back before migration.

## Implementation contract
REQ-001. The schema belongs to the parent PR. The child migration adds an approval flag to stored invoices. Existing amounts and identifiers survive the migration.

## Review
Reviewed: SQLite integer storage, ownership of the parent schema and child migration, preservation of existing rows and the approval default.
Verdict: pass
