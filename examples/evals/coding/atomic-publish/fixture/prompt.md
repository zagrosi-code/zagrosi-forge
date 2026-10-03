# Make release-bundle publication recoverable

`publisher` validates a bundle before writing, but a mid-publication error leaves
the release directory containing a mix of versions. Repair `publish_files` and
`BundlePublisher.write` while preserving the public functions/classes, signatures,
validation errors and order, successful return values, and the callers in
`src/release.py`.

For a valid bundle, stage every document completely in a temporary sibling file
before replacing any destination. Publish each staged file in input order using
`os.replace` (one atomic replacement per destination). Preserve an existing
destination's permission bits. No whole-directory swap is required. Invoke
`on_publish(name, pathlib.Path)` once immediately after each successful replacement,
in input order; it must observe the new complete file. Previously invoked callbacks
are external side effects and are not undone or replayed during rollback.

If staging, replacement, or a callback raises an ordinary `Exception`, restore
every destination already changed by this call to its prior bytes and permission
bits; remove destinations that did not previously exist. Keep unrelated files
untouched. Remove this call's staging/backup files on success or failure. Re-raise
the original exception object. Assume no concurrent writers and that rollback
filesystem operations succeed; crash recovery, durability across power loss,
`BaseException`, and callbacks that themselves change files are out of scope.

Keep complete validation before any write or callback, including one-shot inputs,
duplicate filenames, and symlink/non-file targets. `publish_text` retains UTF-8
encoding and its existing eager conversion/validation order. Empty bundles remain
a no-op against an existing output directory. Inputs must not be mutated.

The two publishing entrypoints currently duplicate orchestration. Add regression
tests for uncovered behavior and reduce that duplication while implementing the
fix. Keep code cohesive, use only Python's standard library and existing unittest
discovery, and leave public callers unchanged.
