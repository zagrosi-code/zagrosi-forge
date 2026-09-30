from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from forge_test_helpers import (
    ROOT,
    load_zagrosi_module,
    run_cmd,
    run_raw,
)
from test_resume_guidance import documented_detached_plan


def test_capability_inventory_redacts_secrets_and_reports_tools(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[plugins."github@openai-curated"]\n'
        "enabled = true\n\n"
        '[plugins."zagrosi-forge@zagrosi"]\n'
        "enabled = true\n\n"
        "[mcp_servers.context7]\n"
        'url = "https://mcp.context7.com/mcp"\n\n'
        "[mcp_servers.context7.http_headers]\n"
        'CONTEXT7_API_KEY = "SECRET-DO-NOT-LEAK"\n'
    )

    payload = run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(config))
    serialized = json.dumps(payload)

    assert payload["success"] is True
    assert payload["config_scope"] == "codex"
    assert "SECRET-DO-NOT-LEAK" not in serialized
    assert {"gh", "codex", "claude", "gemini"} <= set(payload["local_tools"])
    assert "github@openai-curated" in {item["id"] for item in payload["plugins"]["configured"]}
    assert "zagrosi-forge@zagrosi" in {item["id"] for item in payload["plugins"]["configured"]}
    assert "context7" in {item["name"] for item in payload["mcp_servers"]["configured"]}
    assert payload["recommendations"]


def test_capability_inventory_handles_missing_config(tmp_path: Path) -> None:
    payload = run_cmd("capability-inventory", "--plugin-root", str(ROOT), "--config", str(tmp_path / "missing.toml"))

    assert payload["success"] is True
    assert {"gh", "codex", "claude", "gemini"} <= set(payload["local_tools"])
    assert payload["warnings"]


def test_review_capabilities_preserves_codex_baseline_alias(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "external_llm"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "external_llm"
    assert payload["baseline"]["codex_review"]["available"] is True
    assert payload["baseline"]["codex_review"]["mandatory"] is True
    assert payload["baseline"]["codex_review"] == {
        **payload["baseline"]["agent_review"], "alias_for": "agent_review",
    }
    assert payload["external"]
    assert {item["execution"] for item in payload["external"].values()} <= {"opt_in", "not_configured"}


def test_review_capabilities_warns_on_skip_mode(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "skip"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "skip"
    assert any("skip" in item.lower() and "review" in item.lower() for item in payload["recommendations"])


@pytest.mark.parametrize("mode", [None, "external_llm"])
def test_review_capabilities_works_without_vendor_clis(tmp_path: Path, mode: str | None) -> None:
    if mode:
        (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": mode}))
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()

    payload = run_cmd(
        "review-capabilities", "--planning-dir", str(tmp_path),
        env={**os.environ, "PATH": str(empty_path)},
    )

    assert payload["configured_mode"] == (mode or "agent_review")
    assert payload["baseline"]["agent_review"] == {
        "available": True, "mandatory": True, "execution": "agent_review",
    }
    assert set(payload["external"]) == {"codex", "claude", "gemini"}
    assert all(
        item == {"available": False, "path": None, "execution": "not_configured"}
        for item in payload["external"].values()
    )
    if mode == "external_llm":
        assert any("active-agent review" in item for item in payload["recommendations"])


def test_review_capabilities_never_executes_detected_model_clis(monkeypatch, capsys) -> None:
    forge = load_zagrosi_module()
    monkeypatch.setattr(forge.capabilities.shutil, "which", lambda name: f"/unused/bin/{name}")

    def reject_execution(*args, **kwargs):
        pytest.fail("Capability inspection must not execute model CLIs.")

    monkeypatch.setattr(subprocess, "run", reject_execution)
    assert forge.capabilities.review_capabilities(argparse.Namespace(planning_dir=None, config=None)) == 0
    payload = json.loads(capsys.readouterr().out)

    assert set(payload["external"]) == {"codex", "claude", "gemini"}
    assert all(item["available"] and item["execution"] == "opt_in" for item in payload["external"].values())


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_plan_setup_defaults_to_agent_review_at_every_depth(tmp_path: Path, depth: str) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n\nREQ-001: Normalize whitespace in an input string.\n")

    payload = run_cmd("plan-setup", "--file", str(spec), "--depth", depth, "--flight", "off")
    config = json.loads((tmp_path / "zagrosi_plan_config.json").read_text())

    assert payload["review_mode"] == config["review_mode"] == "agent_review"
    assert payload["depth_mode"] == config["depth_mode"] == depth


@pytest.mark.parametrize("mode", ["agent_review", "codex_review", "external_llm", "skip"])
def test_plan_setup_preserves_explicit_review_mode_on_resume(tmp_path: Path, mode: str) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n\nREQ-001: Normalize whitespace in an input string.\n")
    setup = run_cmd("plan-setup", "--file", str(spec), "--review-mode", mode, "--flight", "off")
    resumed = run_cmd("plan-setup", "--file", str(spec), "--flight", "off")
    capabilities = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert setup["review_mode"] == resumed["review_mode"] == capabilities["configured_mode"] == mode
    assert json.loads((tmp_path / "zagrosi_plan_config.json").read_text())["review_mode"] == mode


@pytest.mark.parametrize("mode", [None, "agent_review", "codex_review", "skip"])
def test_review_modes_preserve_missing_review_admission(tmp_path: Path, mode: str | None) -> None:
    planning = documented_detached_plan(tmp_path / "plan", "lean")
    (planning / "reviews/codex.md").unlink()
    if mode:
        (planning / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": mode}))

    result = run_raw("lint-plan-artifacts", "--planning-dir", str(planning), "--strict")
    payload = json.loads(result.stdout)

    assert result.returncode == (0 if mode == "skip" else 1)
    assert payload["success"] is (mode == "skip")
    assert ("missing-review" in {item["code"] for item in payload["findings"]}) is (mode != "skip")
