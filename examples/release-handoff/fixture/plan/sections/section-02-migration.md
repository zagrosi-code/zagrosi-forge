# Approval migration

## Owned files
- migration.sql
- check.py

## Tests first
`python check.py migration` applies the migration to a populated database and expects the existing invoice to keep its amount and start unapproved.
