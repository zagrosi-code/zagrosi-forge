# Reliable webhook retry

A failed delivery is silently skipped on retry. Two tenants with the same event
ID also interfere with each other. Fix `dispatch_batch` without changing its
signature, return shape, validation order, error messages, audit ordering or
payload-copy behavior.

The caller-owned `completed` set must contain `(tenant, id)` tuples, added only
after `send` returns successfully. Skip already completed tuples, including
repeated events within one batch. Preserve any unrelated entries in the set.
Propagate the exact send exception; retain only successfully delivered progress
and audit rows. A later retry of the original batch must deliver every remaining
event in order without resending successful ones. Every batch must be validated
fully before any side effects. Inputs must stay unchanged even if `send` mutates
its payload. Empty batches and duplicate counts retain their existing meaning.

This is a synchronous in-memory journal; do not add persistence, threads,
automatic retries, dependencies or an exactly-once crash guarantee. Add useful
regressions and improve encountered code only where the fix warrants it.
