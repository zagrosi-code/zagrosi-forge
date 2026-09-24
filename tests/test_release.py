from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from forge_test_helpers import (
    ROOT,
    load_zagrosi_module,
    run_cmd,
    run_raw,
)


def package_without_examples(path):
    manifest_path = Path(".codex-plugin/package-files.json")
    members = [name for name in json.loads((ROOT / manifest_path).read_text()) if not name.startswith("examples/")]
    for name in members:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (path / manifest_path).write_text(json.dumps(members, indent=2) + "\n")
    return path


def test_release_check_skips_example_gates_when_examples_are_absent(tmp_path: Path) -> None:
    package = package_without_examples(tmp_path / "bundle")

    release = run_cmd("release-check", "--plugin-root", str(package), "--verbose")

    assert release["success"] is True
    command_text = "\n".join(row["command"] for row in release["results"])
    assert "examples/evals/suite.json" not in command_text
    assert "lint-project-manifest" not in command_text
    assert "eval-suite" not in command_text
    assert ".agents/plugins/marketplace.json" in command_text


def test_release_check_success_is_byte_bounded_and_verbose_is_explicit(tmp_path: Path) -> None:
    package = package_without_examples(tmp_path / "bundle")

    compact = run_raw("release-check", "--plugin-root", str(package))

    assert compact.returncode == 0, compact.stderr + compact.stdout
    assert len(compact.stdout.encode()) <= 1_000
    payload = json.loads(compact.stdout)
    assert payload["success"] is True
    assert payload["check_count"] == len(payload["checks"])
    assert payload["duration_seconds"] >= 0
    assert "results" not in payload
    assert "validate-marketplace" in payload["checks"]
    assert "install-dry-run" in payload["checks"]
    assert "doctor" not in payload["checks"]


def test_release_check_failure_always_includes_command_diagnostics(tmp_path: Path) -> None:
    broken_plugin = tmp_path / "broken-plugin"
    broken_plugin.mkdir()

    result = run_raw("release-check", "--plugin-root", str(broken_plugin))

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["results"]
    failed = [row for row in payload["results"] if row["returncode"] != 0]
    assert failed
    assert all(row["command"] for row in failed)
    assert all("stdout_tail" in row and "stderr_tail" in row for row in failed)


def test_release_postflight_allows_the_release_check_timeout_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_zagrosi_module()
    captured: dict[str, object] = {}

    def fake_gate(name: str, command: list[str], **kwargs: object) -> dict[str, object]:
        captured.update(name=name, command=command, **kwargs)
        return {
            "name": name,
            "required": True,
            "success": True,
            "payload": {
                "check_count": 4,
                "checks": ["compile-cli", "validate-plugin-manifest", "validate-marketplace", "install-dry-run"],
                "duration_seconds": 0.2,
                "results": [{"command": "too verbose"}],
            },
        }

    monkeypatch.setattr(module.gates, "run_internal_gate", fake_gate)
    report = module.flights.release_postflight_report(
        Path("/tmp/plugin"),
        SimpleNamespace(flight="strict", run_tests=True),
    )

    assert report["success"] is True
    assert captured["timeout_seconds"] == 600
    assert "--run-tests" in captured["command"]
    assert report["gates"][0]["payload"] == {
        "check_count": 4,
        "checks": ["compile-cli", "validate-plugin-manifest", "validate-marketplace", "install-dry-run"],
        "duration_seconds": 0.2,
    }
