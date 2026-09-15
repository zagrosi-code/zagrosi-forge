from __future__ import annotations

import json
from pathlib import Path

from forge_test_helpers import (
    load_zagrosi_module,
    run_cmd,
    run_raw,
    write_implementation_drift_fixture,
)


def test_implementation_drift_uses_latest_exact_owner_and_ignores_prose_paths(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-tooling", "section-02-config-correction"),
        {
            "section-01-tooling": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_tooling.py\n"
                "```\n"
            ),
            "section-02-config-correction": (
                "# Section 02\n\n"
                "The correction is verified separately in `tests/test_release.py`.\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "```\n\n"
                "## Verification\n\n"
                "Run `tests/test_release.py` after updating the package.\n"
            ),
        },
    )
    diff_file = tmp_path / "config.diff"
    diff_file.write_text(
        "diff --git a/apps/web/package.json b/apps/web/package.json\n"
        "+++ b/apps/web/package.json\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["success"] is True
    assert drift["active_sections"] == ["section-02-config-correction"]
    assert drift["planned_files"] == ["apps/web/package.json", "tests/test_tooling.py"]
    assert drift["planned_tests"] == []
    assert drift["changed_tests"] == []
    assert "planned-tests-not-changed" not in {
        item["code"] for item in drift["findings"]
    }


def test_implementation_drift_latest_owner_still_requires_its_test(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-tooling", "section-02-config-correction"),
        {
            "section-01-tooling": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_tooling.py\n"
                "```\n"
            ),
            "section-02-config-correction": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_config_correction.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "config.diff"
    diff_file.write_text(
        "diff --git a/apps/web/package.json b/apps/web/package.json\n"
        "+++ b/apps/web/package.json\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
    )

    assert drift["active_sections"] == ["section-02-config-correction"]
    assert drift["planned_tests"] == ["tests/test_config_correction.py"]
    assert drift["changed_tests"] == []
    assert drift["missing_planned_tests"] == ["tests/test_config_correction.py"]
    assert "planned-tests-not-changed" in {
        item["code"] for item in drift["findings"]
    }


def test_implementation_drift_ignores_test_superseded_by_later_owner(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-api", "section-02-test-correction"),
        {
            "section-01-api": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/api.py\n"
                "tests/test_shared.py\n"
                "```\n"
            ),
            "section-02-test-correction": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "tests/test_shared.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "api.diff"
    diff_file.write_text(
        "diff --git a/src/api.py b/src/api.py\n"
        "+++ b/src/api.py\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-01-api"]
    assert drift["planned_tests"] == []
    assert drift["changed_tests"] == []
    assert drift["sections_missing_changed_tests"] == []


def test_implementation_drift_ignores_unmanifested_section_files(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-tooling", "section-02-config-correction"),
        {
            "section-01-tooling": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_tooling.py\n"
                "```\n"
            ),
            "section-02-config-correction": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "```\n"
            ),
            "section-99-stray": (
                "# Stray section\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "apps/web/package.json\n"
                "tests/test_stray.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "config.diff"
    diff_file.write_text(
        "diff --git a/apps/web/package.json b/apps/web/package.json\n"
        "+++ b/apps/web/package.json\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-02-config-correction"]
    assert drift["planned_files"] == ["apps/web/package.json", "tests/test_tooling.py"]
    assert drift["planned_tests"] == []
    assert "tests/test_stray.py" not in drift["planned_files"]


def test_implementation_drift_rejects_incomplete_manifest_without_traceback(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-app", "section-02-missing"),
        {
            "section-01-app": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/app.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "app.diff"
    diff_file.write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "+++ b/src/app.py\n"
    )

    result = run_raw(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["section_progress"]["state"] == "partial"
    assert payload["section_progress"]["missing"] == ["section-02-missing"]
    assert "invalid-sections" in {item["code"] for item in payload["findings"]}


def test_implementation_drift_does_not_count_out_of_scope_test_as_changed_test(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-app",),
        {
            "section-01-app": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/app.py\n"
                "tests/test_app.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "app.diff"
    diff_file.write_text(
        "diff --git a/src/app.py b/src/app.py\n"
        "+++ b/src/app.py\n"
        "diff --git a/tests/unplanned.py b/tests/unplanned.py\n"
        "+++ b/tests/unplanned.py\n"
    )

    result = run_raw(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["active_sections"] == ["section-01-app"]
    assert payload["planned_tests"] == ["tests/test_app.py"]
    assert payload["changed_tests"] == []
    assert payload["missing_planned_tests"] == ["tests/test_app.py"]
    assert payload["out_of_scope"] == ["tests/unplanned.py"]
    codes = {item["code"] for item in payload["findings"]}
    assert {"implementation-drift-file", "planned-tests-not-changed"} <= codes


def test_implementation_drift_requires_changed_test_for_each_active_section(
    tmp_path: Path,
) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-api", "section-02-worker"),
        {
            "section-01-api": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/api.py\n"
                "tests/test_api.py\n"
                "```\n"
            ),
            "section-02-worker": (
                "# Section 02\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/worker.py\n"
                "tests/test_worker.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "multi-section.diff"
    diff_file.write_text(
        "diff --git a/tests/test_api.py b/tests/test_api.py\n"
        "+++ b/tests/test_api.py\n"
        "diff --git a/src/worker.py b/src/worker.py\n"
        "+++ b/src/worker.py\n"
    )

    result = run_raw(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["active_sections"] == ["section-01-api", "section-02-worker"]
    assert payload["changed_tests"] == ["tests/test_api.py"]
    assert payload["missing_planned_tests"] == ["tests/test_worker.py"]
    assert payload["sections_missing_changed_tests"] == ["section-02-worker"]
    findings = [
        item for item in payload["findings"]
        if item["code"] == "planned-tests-not-changed"
    ]
    assert len(findings) == 1
    assert "section-02-worker" in findings[0]["message"]


def test_implementation_drift_preserves_legacy_prose_path_fallback(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-legacy",),
        {
            "section-01-legacy": (
                "# Legacy section\n\n"
                "Implement `src/legacy.py` and verify it in `tests/test_legacy.py`.\n"
            ),
        },
    )
    diff_file = tmp_path / "legacy.diff"
    diff_file.write_text(
        "diff --git a/src/legacy.py b/src/legacy.py\n"
        "+++ b/src/legacy.py\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
    )

    assert drift["active_sections"] == ["section-01-legacy"]
    assert drift["planned_files"] == ["src/legacy.py", "tests/test_legacy.py"]
    assert drift["planned_tests"] == ["tests/test_legacy.py"]


def test_implementation_drift_counts_storybook_story_as_changed_test(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-shell",),
        {
            "section-01-shell": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/ui/foundation.stories.tsx\n"
                "tests/shell.spec.ts\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "story.diff"
    diff_file.write_text(
        "diff --git a/src/ui/foundation.stories.tsx b/src/ui/foundation.stories.tsx\n"
        "+++ b/src/ui/foundation.stories.tsx\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-01-shell"]
    assert drift["changed_tests"] == ["src/ui/foundation.stories.tsx"]
    assert drift["planned_tests"] == [
        "src/ui/foundation.stories.tsx",
        "tests/shell.spec.ts",
    ]


def test_implementation_drift_does_not_treat_substring_matches_as_tests(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    write_implementation_drift_fixture(
        planning,
        ("section-01-source",),
        {
            "section-01-source": (
                "# Section 01\n\n"
                "## Exact path ownership\n\n"
                "```text\n"
                "src/latest.py\n"
                "src/contest.py\n"
                "src/specification.py\n"
                "```\n"
            ),
        },
    )
    diff_file = tmp_path / "source.diff"
    diff_file.write_text(
        "diff --git a/src/latest.py b/src/latest.py\n"
        "+++ b/src/latest.py\n"
    )

    drift = run_cmd(
        "implementation-drift",
        "--planning-dir",
        str(planning),
        "--diff-file",
        str(diff_file),
        "--strict",
    )

    assert drift["active_sections"] == ["section-01-source"]
    assert drift["planned_tests"] == []
    assert drift["changed_tests"] == []


def test_test_path_classifier_accepts_story_modules_and_rejects_story_prose() -> None:
    module = load_zagrosi_module()

    for suffix in ("js", "jsx", "mjs", "ts", "tsx"):
        assert module.markdown.is_test_path(f"src/ui/foundation.stories.{suffix}") is True
    for path in (
        "tests/test_auth.py",
        "src/auth.test.ts",
        "src/auth_spec.rb",
        "src/test/java/AuthTest.java",
        "src/spec/helpers.rb",
        "src/FooTest.java",
        "src/TestOAuth.java",
        "conftest.py",
    ):
        assert module.markdown.is_test_path(path) is True
    for path in (
        "docs/user-stories.md",
        "src/ui/user.stories.md",
        "src/ui/foundation.stories.tsx.backup",
        "src/ui/foundation.story.tsx",
        "src/latest.py",
        "src/contest.py",
        "src/special.py",
        "src/specification.py",
        "src/testing.py",
        "src/unittest.py",
    ):
        assert module.markdown.is_test_path(path) is False
