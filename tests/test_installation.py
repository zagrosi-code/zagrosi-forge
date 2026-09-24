from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from forge_test_helpers import (
    PLUGIN_VERSION,
    ROOT,
    run_cmd,
    run_script_raw,
)


def test_install_codex_updates_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[plugins."other@example"]\nenabled = true\n')

    dry_run = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--dry-run",
        "--no-verify-codex",
    )
    assert dry_run["success"] is True
    assert dry_run["changed"] is True
    assert "[marketplaces.zagrosi]" in dry_run["config_preview"]
    assert "[marketplaces.zagrosi]" not in config.read_text()
    assert dry_run["cache"]["changed"] is True
    assert not Path(dry_run["cache"]["path"]).exists()

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--no-verify-codex",
    )
    assert installed["success"] is True
    assert installed["changed"] is True
    assert installed["config_changed"] is True
    assert installed["backup_path"]
    updated = config.read_text()
    assert "[marketplaces.zagrosi]" in updated
    assert f'source = "{ROOT}"' in updated
    assert '[plugins."zagrosi-forge@zagrosi"]' in updated
    assert "enabled = true" in updated
    assert Path(installed["backup_path"]).exists()
    cache_path = Path(installed["cache"]["path"])
    assert cache_path == tmp_path / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / PLUGIN_VERSION
    assert (cache_path / ".codex-plugin" / "plugin.json").exists()
    assert (cache_path / "skills" / "zagrosi-project" / "SKILL.md").exists()

    repeated = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--no-verify-codex",
    )
    assert repeated["success"] is True
    assert repeated["changed"] is False
    assert repeated["cache"]["changed"] is False
    assert repeated["backup_path"] is None


def test_update_check_reports_cache_and_config_status(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"

    status = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config))

    assert status["success"] is True
    assert status["operation"] == "update-check"
    assert status["network_policy"] == "local-only"
    assert status["remote_checked"] is False
    assert status["cache"]["current"] is False
    assert status["cache"]["exists"] is False
    assert status["cache"]["changed"] is True
    assert Path(status["cache"]["path"]) == tmp_path / "codex" / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / PLUGIN_VERSION
    assert status["config"]["current"] is False
    assert status["restart_required"] is True
    assert any("self-update" in item for item in status["next_steps"])
    assert not config.exists()


@pytest.mark.parametrize("manifest_text", [
    None,
    "{",
    "{}",
    '[".codex-plugin/package-files.json", "../escape.txt"]',
    '[".codex-plugin/package-files.json", {}]',
    '[".codex-plugin/package-files.json", "missing.txt"]',
], ids=["missing", "invalid-json", "wrong-shape", "unsafe-entry", "non-string-entry", "missing-member"])
@pytest.mark.parametrize("existing_setup", [False, True], ids=["absent-setup", "existing-setup"])
def test_update_check_invalid_package_returns_json_without_mutation(tmp_path, manifest_text, existing_setup):
    plugin = tmp_path / "plugin"
    manifest_name = ".codex-plugin/package-files.json"
    for name in json.loads((ROOT / manifest_name).read_text()):
        destination = plugin / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    manifest = plugin / manifest_name
    if manifest_text is None:
        manifest.unlink()
    else:
        manifest.write_text(manifest_text)

    codex_dir = tmp_path / "codex"
    cache = codex_dir / "plugins/cache/zagrosi/zagrosi-forge" / PLUGIN_VERSION
    config = codex_dir / "config.toml"
    if existing_setup:
        cache.mkdir(parents=True)
        (cache / "keep.txt").write_bytes(b"existing cached plugin\n")
        config.write_bytes(b'# preserve config bytes\r\n[plugins."other@example"]\r\nenabled = true\r\n')

    def snapshot():
        return codex_dir.exists(), {
            path.relative_to(codex_dir): path.read_bytes() if path.is_file() else None
            for path in codex_dir.rglob("*")
        }

    before = snapshot()
    result = run_script_raw(plugin / "scripts/zagrosi_skills.py", "update-check",
                            "--plugin-root", str(plugin), "--config", str(config))
    assert snapshot() == before
    assert result.returncode == 1
    assert "Traceback" not in result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["operation"] == "update-check"
    assert isinstance(payload["error"], str) and payload["error"]


def test_self_update_materializes_cache_and_update_check_passes(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"

    updated = run_cmd("self-update", "--plugin-root", str(ROOT), "--config", str(config), "--no-verify-codex")

    assert updated["success"] is True
    assert updated["operation"] == "self-update"
    assert updated["changed"] is True
    cache_path = Path(updated["cache"]["path"])
    assert cache_path == tmp_path / "codex" / "plugins" / "cache" / "zagrosi" / "zagrosi-forge" / PLUGIN_VERSION
    assert not (cache_path / "planning").exists()
    assert config.exists()

    status = run_cmd("update-check", "--plugin-root", str(ROOT), "--config", str(config))

    assert status["success"] is True
    assert status["cache"]["current"] is True
    assert status["cache"]["changed"] is False
    assert status["config"]["current"] is True
    assert status["restart_required"] is False
    assert any("already current" in item.lower() for item in status["next_steps"])


def test_install_codex_verifies_prompt_input_with_cached_plugin(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"debug\" ] && [ \"$2\" = \"prompt-input\" ]; then\n"
        "  printf '%s\\n' 'zagrosi-forge:zagrosi-project' 'zagrosi-forge:zagrosi-plan' 'zagrosi-forge:zagrosi-implement'\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    installed = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        "--verify-codex",
        env=env,
    )

    assert installed["success"] is True
    assert installed["verification"]["status"] == "passed"
    assert installed["verification"]["missing"] == []

    fake_codex.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    unchanged = run_cmd(
        "install",
        "--plugin-root",
        str(ROOT),
        "--config",
        str(config),
        env=env,
    )
    assert unchanged["success"] is True
    assert unchanged["changed"] is False
    assert unchanged["verification"]["status"] == "skipped"
    assert unchanged["verification"]["reason"] == "installation unchanged"
