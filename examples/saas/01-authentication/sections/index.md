<!-- PROJECT_CONFIG
runtime: python
test_command: uv run pytest
END_PROJECT_CONFIG -->

<!-- SECTION_MANIFEST
section-01-oauth-foundation
END_MANIFEST -->

# Sections

| Section | Dependencies | Execution order | Parallel |
|---|---|---|---|
| section-01-oauth-foundation | none | 1 | no; shared auth boundary |

REQ-001: tests -> config -> callback -> session. Verify targeted, then full pytest.
