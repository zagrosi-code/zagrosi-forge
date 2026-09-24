"""Configuration edits preserve unrelated bytes and publish only complete files."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tomllib

import pytest

from forge_test_helpers import ROOT, SCRIPT, run_raw
from runtime_support import load_runtime


@pytest.fixture
def installer():
    return load_runtime(SCRIPT).installation


@pytest.mark.parametrize("header", ["[marketplaces.'zagrosi']", '[ marketplaces . "zagrosi" ]'])
def test_equivalent_headers_preserve_unrelated_bytes(installer, tmp_path, header):
    existing = '# keep spacing\r\nsecret  = "canary"\r\n' + header + '\r\nsource_type="local"\r\nsource="/old"\r\n'
    updated, changes = installer.expected_codex_config(existing, tmp_path)
    assert tomllib.loads(updated)["marketplaces"]["zagrosi"]["source"] == str(tmp_path)
    assert updated.startswith('# keep spacing\r\nsecret  = "canary"\r\n' + header + '\r\n')
    assert changes


def test_current_inline_config_remains_byte_identical(installer, tmp_path):
    existing = f'marketplaces = {{zagrosi = {{source_type = "local", source = {json.dumps(str(tmp_path))}}}}}\nplugins = {{"zagrosi-forge@zagrosi" = {{enabled = true}}}}\n'
    assert installer.expected_codex_config(existing, tmp_path) == (existing, [])


@pytest.mark.parametrize("existing", [
    'marketplaces = {zagrosi = {source_type = "local", source = "/old"}}\n',
    'marketplaces.zagrosi.source = "/old"\n',
    'secret = "SYNTHETIC_CONFIG_CANARY"\n[broken\n',
])
def test_unsupported_or_invalid_config_is_refused_before_install(tmp_path, existing):
    config = tmp_path / "config.toml"
    config.write_text(existing)
    result = run_raw("install", "--plugin-root", str(ROOT), "--config", str(config), "--no-verify-codex")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert "native plugin installer" in payload["error"]
    assert config.read_text() == existing
    assert not (tmp_path / "plugins").exists()
    assert "SYNTHETIC_CONFIG_CANARY" not in result.stdout + result.stderr


def test_dry_run_exposes_only_managed_values(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('secret = "SYNTHETIC_CONFIG_CANARY"\n[plugins."other@example"]\nenabled = true\n')
    before = config.read_bytes()
    result = run_raw("install", "--plugin-root", str(ROOT), "--config", str(config), "--dry-run", "--no-verify-codex")
    assert result.returncode == 0
    assert "SYNTHETIC_CONFIG_CANARY" not in result.stdout + result.stderr
    preview = tomllib.loads(json.loads(result.stdout)["config_preview"])
    assert set(preview) == {"marketplaces", "plugins"}
    assert set(preview["plugins"]) == {"zagrosi-forge@zagrosi"}
    assert config.read_bytes() == before


@pytest.mark.parametrize("enabled", ["1", "false"])
def test_owned_boolean_requires_exact_type(installer, tmp_path, enabled):
    existing = f'[plugins."zagrosi-forge@zagrosi"]\nenabled={enabled}\n'
    updated, _ = installer.expected_codex_config(existing, tmp_path)
    assert tomllib.loads(updated)["plugins"]["zagrosi-forge@zagrosi"]["enabled"] is True


def test_unrelated_nan_and_multiline_text_survive(installer, tmp_path):
    existing = 'ratio = nan\nnotes = """\n[marketplaces.zagrosi]\nsource="do not edit"\n"""\n'
    with pytest.raises(ValueError, match="native plugin installer"):
        installer.expected_codex_config(existing, tmp_path)
    plain = "ratio = nan\n"
    updated, _ = installer.expected_codex_config(plain, tmp_path)
    assert updated.startswith(plain)


def test_atomic_failure_preserves_config_and_restrictive_backup(installer, tmp_path, monkeypatch):
    owner = installer._codex_config
    config = tmp_path / "config.toml"
    original = b'# original bytes\nsecret = "SYNTHETIC_CONFIG_CANARY"\n'
    config.write_bytes(original)
    config.chmod(0o640)
    snapshot = owner.read_config(config)
    updated, _ = installer.expected_codex_config(original.decode(), tmp_path)

    def fail_replace(*_):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(owner.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        owner.publish_config(config, snapshot, updated)
    assert config.read_bytes() == original
    backups = list(tmp_path.glob("config.toml.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    if os.name != "nt":
        assert backups[0].stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".config.toml.*"))


def test_atomic_publication_preserves_permissions_and_refuses_drift(installer, tmp_path):
    owner = installer._codex_config
    config = tmp_path / "config.toml"
    config.write_text("# original\n")
    config.chmod(0o640)
    snapshot = owner.read_config(config)
    updated, _ = installer.expected_codex_config(snapshot[0].decode(), tmp_path)
    config.write_text("# concurrent edit\n")
    with pytest.raises(ValueError, match="native plugin installer"):
        owner.publish_config(config, snapshot, updated)
    assert config.read_text() == "# concurrent edit\n"
    assert not list(tmp_path.glob("config.toml.bak-*"))
    snapshot = owner.read_config(config)
    updated, _ = installer.expected_codex_config(snapshot[0].decode(), tmp_path)
    owner.publish_config(config, snapshot, updated)
    assert config.read_text() == updated
    if os.name != "nt":
        assert config.stat().st_mode & 0o777 == 0o640


def test_configuration_symlink_is_refused(installer, tmp_path):
    target = tmp_path / "target.toml"
    target.write_text("# untouched\n")
    link = tmp_path / "config.toml"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Platform does not permit symlink creation")
    with pytest.raises(ValueError, match="native plugin installer"):
        installer._codex_config.read_config(link)
    assert target.read_text() == "# untouched\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX interruption and permissions")
def test_interrupted_publication_retains_original_and_releases_lock(tmp_path):
    config = tmp_path / "config.toml"
    original = b'secret = "SYNTHETIC_CONFIG_CANARY"\n'
    config.write_bytes(original)
    program = """
import os, signal, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "tests"))
from runtime_support import load_runtime
runtime = load_runtime(Path(sys.argv[1]) / "scripts/zagrosi_skills.py")
owner = runtime.codex_config
config = Path(sys.argv[2])
with runtime.storage.file_lock(config):
    snapshot = owner.read_config(config)
    updated, _ = owner.expected_codex_config(snapshot[0].decode(), Path(sys.argv[1]))
    owner.os.replace = lambda *_: os.kill(os.getpid(), signal.SIGKILL)
    owner.publish_config(config, snapshot, updated)
"""
    killed = subprocess.run([sys.executable, "-c", program, str(ROOT), str(config)], capture_output=True, text=True)
    assert killed.returncode == -signal.SIGKILL
    assert config.read_bytes() == original
    assert list(tmp_path.glob("config.toml.bak-*"))[0].read_bytes() == original
    assert "SYNTHETIC_CONFIG_CANARY" not in killed.stdout + killed.stderr
    result = run_raw("install", "--plugin-root", str(ROOT), "--config", str(config), "--no-verify-codex")
    assert result.returncode == 0, result.stdout + result.stderr
    assert tomllib.loads(config.read_text())["plugins"]["zagrosi-forge@zagrosi"]["enabled"] is True


def test_concurrent_installs_keep_unrelated_config_bytes(tmp_path):
    config = tmp_path / "config.toml"
    prefix = '# user-owned bytes\nsecret = "SYNTHETIC_CONFIG_CANARY"\n'
    config.write_text(prefix)
    command = [sys.executable, str(SCRIPT), "install", "--plugin-root", str(ROOT),
               "--config", str(config), "--no-verify-codex"]
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = [process.communicate(timeout=30) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    payloads = [json.loads(stdout) for stdout, _ in results]
    assert sorted(payload["config_changed"] for payload in payloads) == [False, True]
    assert config.read_text().startswith(prefix)
    assert tomllib.loads(config.read_text())["plugins"]["zagrosi-forge@zagrosi"]["enabled"] is True
    assert len(list(tmp_path.glob("config.toml.bak-*"))) == 1
    assert all("SYNTHETIC_CONFIG_CANARY" not in stdout + stderr for stdout, stderr in results)


def test_verification_never_echoes_config_values(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda _: "/fake/codex")
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args, 1, "SYNTHETIC_CONFIG_CANARY", "SYNTHETIC_CONFIG_CANARY",
    ))
    verification = installer.verify_codex_install(tmp_path, True)
    assert verification["success"] is False
    assert "SYNTHETIC_CONFIG_CANARY" not in json.dumps(verification)
