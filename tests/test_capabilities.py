from __future__ import annotations

import json
from pathlib import Path

from forge_test_helpers import (
    ROOT,
    run_cmd,
)


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


def test_review_capabilities_reports_mandatory_codex_fallback(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "external_llm"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "external_llm"
    assert payload["baseline"]["codex_review"]["available"] is True
    assert payload["baseline"]["codex_review"]["mandatory"] is True
    assert payload["external"]
    assert {item["execution"] for item in payload["external"].values()} <= {"opt_in", "not_configured"}


def test_review_capabilities_warns_on_skip_mode(tmp_path: Path) -> None:
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"review_mode": "skip"}))

    payload = run_cmd("review-capabilities", "--planning-dir", str(tmp_path))

    assert payload["success"] is True
    assert payload["configured_mode"] == "skip"
    assert any("skip" in item.lower() and "review" in item.lower() for item in payload["recommendations"])
