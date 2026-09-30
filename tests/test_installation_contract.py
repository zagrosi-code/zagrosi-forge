"""Installation must verify every advertised skill before reporting success."""

import argparse
import json
import subprocess

import pytest

from forge_test_helpers import load_zagrosi_module


SKILLS = [f"zagrosi-forge:{name}" for name in (
    "zagrosi-forge", "zagrosi-project", "zagrosi-plan", "zagrosi-implement", "zagrosi-cleanup",
)]


@pytest.mark.parametrize("missing", [None, *SKILLS])
def test_native_verification_requires_every_advertised_skill(tmp_path, monkeypatch, missing):
    installer = load_zagrosi_module().installation
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/fake/codex")
    discovered = "\n".join(skill for skill in SKILLS if skill != missing)
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, discovered + "\n" + args[0][-1], ""))

    result = installer.verify_codex_install(tmp_path, True)

    assert result["required_skills"] == SKILLS
    assert result["success"] is (missing is None)
    assert result["missing"] == ([] if missing is None else [missing])


@pytest.mark.parametrize("require_codex", [False, True])
def test_missing_native_cli_still_reports_all_required_skills(tmp_path, monkeypatch, require_codex):
    installer = load_zagrosi_module().installation
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)
    result = installer.verify_codex_install(tmp_path, require_codex)
    assert result["required_skills"] == SKILLS
    assert result["success"] is not require_codex


def test_stalled_doctor_fails_before_installation_mutates_files(tmp_path, monkeypatch, capsys):
    installer = load_zagrosi_module().installation
    plugin = tmp_path / "plugin"
    for name in [".codex-plugin/plugin.json", ".agents/plugins/marketplace.json",
                 *(f"skills/{skill.split(':')[1]}/SKILL.md" for skill in SKILLS)]:
        path = plugin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    script = plugin / "scripts/zagrosi_skills.py"
    script.parent.mkdir()
    script.write_text(
        "import sys, time\nprint('doctor progress', flush=True)\n"
        "print('doctor diagnostic', file=sys.stderr, flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    config = tmp_path / "config.toml"
    original = b'# retain settings\nmodel = "example"\n'
    config.write_bytes(original)
    monkeypatch.setattr(installer, "DOCTOR_TIMEOUT_SECONDS", 1)
    options = argparse.Namespace(plugin_root=str(plugin), config=str(config), command="install",
                                 verify_codex=False, no_verify_codex=True, dry_run=False, no_backup=False)

    assert installer.install_codex(options) == 1

    result = json.loads(capsys.readouterr().out)
    assert not result["success"] and "timed out" in result["error"]
    assert result["doctor"]["timed_out"] and result["doctor"]["returncode"] == 124
    assert "doctor progress" in result["doctor"]["stdout"]
    assert "doctor diagnostic" in result["doctor"]["stderr"]
    assert config.read_bytes() == original
    assert not list(tmp_path.glob("config.toml.bak-*"))
    assert not (tmp_path / "plugins").exists()
