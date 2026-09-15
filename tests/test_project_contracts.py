from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_test_helpers import (
    project_manifest_codes,
    run_raw,
    write_compact_project_fixture,
)


def test_compact_project_manifest_rejects_duplicate_requirement_ownership(tmp_path: Path) -> None:
    planning = write_compact_project_fixture(tmp_path)
    manifest = planning / "project-manifest.md"
    manifest.write_text(manifest.read_text().replace("| 02-billing | REQ-002 |", "| 02-billing | REQ-001, REQ-002 |"))

    assert "duplicate-requirement-owner" in project_manifest_codes(planning)


def test_compact_project_manifest_rejects_dependency_cycles(tmp_path: Path) -> None:
    planning = write_compact_project_fixture(tmp_path)
    manifest = planning / "project-manifest.md"
    manifest.write_text(manifest.read_text().replace("| 01-auth | REQ-001 | none |", "| 01-auth | REQ-001 | 02-billing |"))

    assert "split-dependency-cycle" in project_manifest_codes(planning)


def test_compact_project_manifest_rejects_cross_split_path_collisions(tmp_path: Path) -> None:
    planning = write_compact_project_fixture(tmp_path)
    manifest = planning / "project-manifest.md"
    manifest.write_text(manifest.read_text().replace("| `src/billing.py` |", "| `src/auth.py` |"))

    assert "cross-split-path-collision" in project_manifest_codes(planning)


def test_compact_project_manifest_rejects_owned_requirement_missing_from_spec(tmp_path: Path) -> None:
    planning = write_compact_project_fixture(tmp_path)
    spec = planning / "02-billing" / "spec.md"
    spec.write_text(spec.read_text().replace("REQ-002", "billing behavior"))

    assert "split-spec-missing-requirements" in project_manifest_codes(planning)


def test_compact_project_manifest_rejects_spec_contract_drift(tmp_path: Path) -> None:
    planning = write_compact_project_fixture(tmp_path / "dependencies")
    spec = planning / "02-billing" / "spec.md"
    spec.write_text(spec.read_text().replace("Dependencies: 01-auth", "Dependencies: none"))

    assert "split-spec-dependency-mismatch" in project_manifest_codes(planning)

    planning = write_compact_project_fixture(tmp_path / "boundary")
    spec = planning / "02-billing" / "spec.md"
    spec.write_text(spec.read_text().replace("Boundary: `src/billing.py`", "Boundary: `src/auth.py`"))

    assert "split-spec-boundary-mismatch" in project_manifest_codes(planning)

    planning = write_compact_project_fixture(tmp_path / "duplicates")
    spec = planning / "02-billing" / "spec.md"
    spec.write_text(spec.read_text() + "\nDependencies: none\nBoundary: `src/auth.py`\n")
    codes = project_manifest_codes(planning)

    assert {"duplicate-spec-dependencies", "duplicate-spec-boundary"} <= codes


def test_compact_project_artifacts_have_upper_budgets(tmp_path: Path) -> None:
    planning = write_compact_project_fixture(tmp_path / "manifest")
    manifest = planning / "project-manifest.md"
    manifest.write_text(manifest.read_text() + (" filler" * 500))

    assert "project-manifest-too-large" in project_manifest_codes(planning)

    planning = write_compact_project_fixture(tmp_path / "spec")
    spec = planning / "02-billing" / "spec.md"
    spec.write_text(spec.read_text() + (" filler" * 500))

    assert "split-spec-too-large" in project_manifest_codes(planning)

    postflight = run_raw("postflight", "--phase", "project", "--planning-dir", str(planning), "--depth", "lean")
    assert postflight.returncode != 0
    assert "lint-project-manifest" in json.loads(postflight.stdout)["blocking_gates"]


@pytest.mark.parametrize("session_dir", [None, ".zagrosi-project", ".deep-project"])
def test_legacy_project_manifest_remains_accepted_when_resumed(tmp_path: Path, session_dir: str | None) -> None:
    (tmp_path / "requirements.md").write_text("# Requirements\n\nREQ-001: Legacy behavior.\n")
    (tmp_path / "project-manifest.md").write_text(
        "<!-- SPLIT_MANIFEST\n01-legacy\nEND_MANIFEST -->\n\n"
        "# Project Manifest\n\n"
        "Execution order: 01-legacy. Dependencies: none. Parallel: no. "
        "Shared concerns: tests. Next: $zagrosi-plan 01-legacy/spec.md.\n"
    )
    split = tmp_path / "01-legacy"
    split.mkdir()
    (split / "spec.md").write_text(
        "# Legacy\n\nREQ-001. In scope: behavior. Out of scope: migration. "
        "Dependencies: none. Boundary: legacy module. Risk: drift. "
        "Tests verify acceptance criteria.\n"
    )
    if session_dir:
        state_dir = tmp_path / session_dir
        state_dir.mkdir()
        (state_dir / "session.json").write_text(
            json.dumps({"initial_file": str(tmp_path / "requirements.md"), "depth_mode": "standard"})
        )

    result = run_raw("lint-project-manifest", "--planning-dir", str(tmp_path), "--strict")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert "missing-ownership-table" not in {item["code"] for item in payload["findings"]}
