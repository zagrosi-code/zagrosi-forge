from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).parents[2]


def test_installer_authority_hard_gate_matches_collected_tests() -> None:
    collected = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/package/test_safe_paths.py",
            "tests/package/test_ownership_authority.py",
            "--collect-only",
            "-q",
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert collected.returncode == 0, collected.stdout
    cases = tuple(
        line
        for line in collected.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    )
    assert cases

    workflow = (ROOT / ".github/workflows/installer-spike.yml").read_text(
        encoding="utf-8"
    )
    assert f"$Cases.Count -ne {len(cases)}" in workflow
    assert f"requires {len(cases)} cases" in workflow


def test_toolchain_verifier_runs_before_distribution_install(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "tools/verify_toolchain.py"),
            "--help",
        ],
        check=False,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    assert "--platform" in result.stdout

    offline = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "tools/verify_toolchain.py"),
            "--tool",
            "codex",
            "--platform",
            "linux-x86_64",
            "--destination",
            str(tmp_path / "toolchain"),
            "--offline",
        ],
        check=False,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert offline.returncode != 0
    assert "Verified tool artifact is unavailable offline" in offline.stdout
    assert "PackageNotFoundError" not in offline.stdout


def test_toolchain_versions_and_platform_hashes_are_exact() -> None:
    from zagrosi_forge.install.toolchain import load_toolchain_lock, select_artifact

    lock = load_toolchain_lock()
    versions = {tool["name"]: tool["version"] for tool in lock["tools"]}
    assert versions == {
        "codex": "0.144.4",
        "plugin-scanner": "2.0.274",
        "uv": "0.11.23",
        "uv-build": "0.11.28",
    }
    for platform in ("linux-x86_64", "macos-arm64", "macos-x86_64", "windows-x86_64"):
        for tool in ("codex", "uv", "uv-build"):
            artifact = select_artifact(lock, tool=tool, platform=platform)
            assert len(artifact["sha256"]) == 64
            int(artifact["sha256"], 16)
            assert artifact["url"].startswith("https://")
        scanner = select_artifact(lock, tool="plugin-scanner", platform=platform)
        assert scanner["platform"] == "any"


def test_toolchain_record_is_canonical_and_contains_no_latest() -> None:
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.toolchain import load_toolchain_lock

    raw = (ROOT / "toolchain.lock.json").read_bytes()
    lock = load_toolchain_lock()
    assert raw == canonical_json_bytes(dict(lock), final_newline=True)
    assert b"latest" not in raw.lower()


def test_verify_artifact_fails_closed_without_execution(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.toolchain import verify_artifact

    artifact = tmp_path / "tool.bin"
    artifact.write_bytes(b"verified bytes")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    verify_artifact(artifact, expected_sha256=digest)
    artifact.write_bytes(b"changed bytes")
    with pytest.raises(ForgeError) as caught:
        verify_artifact(artifact, expected_sha256=digest)
    assert caught.value.code == "tool.hash_mismatch"
    assert caught.value.exit_category == 15


def test_toolchain_offline_mode_never_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.toolchain import acquire_artifact, load_toolchain_lock

    called = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("network attempted")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    lock = load_toolchain_lock()
    with pytest.raises(ForgeError) as caught:
        acquire_artifact(
            lock,
            tool="codex",
            platform="macos-arm64",
            destination=tmp_path,
            offline=True,
        )
    assert caught.value.code == "tool.offline_missing"
    assert not called


@pytest.mark.parametrize(
    "lock",
    (
        {"tools": ({"name": ["codex"], "artifacts": ()},)},
        {
            "tools": (
                {
                    "name": "codex",
                    "artifacts": (
                        {
                            "platform": ["macos-arm64"],
                            "url": "https://example.invalid/codex",
                            "sha256": "a" * 64,
                            "archive_type": "tar.gz",
                            "executable": "codex",
                        },
                    ),
                },
            )
        },
    ),
)
def test_toolchain_rejects_malformed_name_and_platform_types(
    lock: dict[str, object],
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.toolchain import select_artifact

    with pytest.raises(ForgeError) as caught:
        select_artifact(lock, tool="codex", platform="macos-arm64")
    assert caught.value.code == "tool.lock_invalid"
    assert caught.value.exit_category == 15


def test_plugin_scanner_and_runtime_graph_are_locked() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["dependency-groups"]["security"] == ["plugin-scanner==2.0.274"]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    for name in ("plugin-scanner", "cisco-ai-skill-scanner", "cryptography", "rich"):
        assert f'name = "{name}"' in lock
