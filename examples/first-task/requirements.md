# Normalize labels

REQ-001: `labels.normalize(value)` strips only leading/trailing whitespace from
strings. Preserve case and internal whitespace; empty/whitespace-only input
returns an empty string. Preserve the public import and argument name.

REQ-002: Non-string input raises `TypeError("label must be a string")` before
calling any methods on the input. Do not coerce values.

REQ-003: Use the standard library and keep the implementation readable and direct.
Demonstrate the failing baseline, targeted regression coverage, and final passing
`python3 -m unittest discover -s tests`. Use one compact plan section.
