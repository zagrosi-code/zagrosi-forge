from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import re

import pytest


_PROFILE_VARIABLES = (
    "HOME",
    "CODEX_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TMPDIR",
    "TEMP",
    "TMP",
)
_SENTINEL_BYTES = b"zagrosi-package-test-boundary\n"
IsolationEvidence = tuple[Path, Path, bytes, tuple[int, int]]


def _identity(path: Path) -> tuple[int, int]:
    metadata = path.stat(follow_symlinks=False)
    return (metadata.st_dev, metadata.st_ino)


def _assert_sentinel(evidence: IsolationEvidence) -> None:
    _, sentinel, expected_bytes, expected_identity = evidence
    assert sentinel.read_bytes() == expected_bytes
    assert _identity(sentinel) == expected_identity


def _managed_python_bin_dirs(environment: dict[str, str]) -> list[str]:
    roots: list[Path] = []
    if configured := environment.get("UV_PYTHON_INSTALL_DIR"):
        roots.append(Path(configured))
    if data_home := environment.get("XDG_DATA_HOME"):
        roots.append(Path(data_home) / "uv/python")
    if home := environment.get("HOME"):
        roots.append(Path(home) / ".local/share/uv/python")
    if appdata := environment.get("APPDATA"):
        roots.append(Path(appdata) / "uv/python")

    directories: list[str] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for installation in sorted(root.glob("cpython-3.*")):
            for directory in (
                installation / "bin",
                installation / "Scripts",
                installation,
            ):
                if not directory.is_dir():
                    continue
                normalized = os.path.normcase(str(directory))
                if normalized not in seen:
                    seen.add(normalized)
                    directories.append(str(directory))
    return directories


@pytest.fixture(scope="module", autouse=True)
def _package_process_environment(
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
) -> Iterator[IsolationEvidence]:
    module_name = re.sub(r"[^a-z0-9]+", "-", request.module.__name__.lower()).strip("-")
    environment_root = tmp_path_factory.mktemp(f"{module_name}-environment")
    sentinel_root = tmp_path_factory.mktemp(f"{module_name}-sentinel")
    sentinel = sentinel_root / "sentinel.bin"
    sentinel.write_bytes(_SENTINEL_BYTES)
    evidence = (
        environment_root,
        sentinel,
        _SENTINEL_BYTES,
        _identity(sentinel),
    )

    redirected = {
        "HOME": environment_root / "home",
        "CODEX_HOME": environment_root / "codex-home",
        "XDG_CONFIG_HOME": environment_root / "xdg-config",
        "XDG_CACHE_HOME": environment_root / "xdg-cache",
        "XDG_DATA_HOME": environment_root / "xdg-data",
        "USERPROFILE": environment_root / "user-profile",
        "APPDATA": environment_root / "app-data",
        "LOCALAPPDATA": environment_root / "local-app-data",
        "TMPDIR": environment_root / "tmp",
        "TEMP": environment_root / "tmp",
        "TMP": environment_root / "tmp",
    }
    assert tuple(redirected) == _PROFILE_VARIABLES
    for path in set(redirected.values()):
        path.mkdir(parents=True)
    assert all(
        sentinel != path and not sentinel.is_relative_to(path)
        for path in redirected.values()
    )

    config_root = environment_root / "tool-config"
    config_root.mkdir()
    git_config = config_root / "gitconfig"
    pip_config = config_root / "pip.ini"
    git_config.write_text("", encoding="utf-8")
    pip_config.write_text("", encoding="utf-8")

    original = dict(os.environ)
    isolated = {
        key: value
        for key, value in original.items()
        if not key.startswith("GIT_")
        and not key.startswith("UV_")
        and not key.startswith("PIP_")
    }
    isolated.update({key: str(value) for key, value in redirected.items()})
    isolated.update(
        {
            "GIT_CONFIG_GLOBAL": str(git_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PIP_CONFIG_FILE": str(pip_config),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": str(environment_root / "uv-cache"),
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_PYTHON_INSTALL_DIR": str(environment_root / "uv-python"),
        }
    )
    python_dirs = _managed_python_bin_dirs(original)
    if python_dirs:
        isolated["PATH"] = os.pathsep.join(
            [*python_dirs, isolated.get("PATH", os.defpath)]
        )

    os.environ.clear()
    os.environ.update(isolated)
    try:
        _assert_sentinel(evidence)
        yield evidence
    finally:
        try:
            _assert_sentinel(evidence)
        finally:
            os.environ.clear()
            os.environ.update(original)


@pytest.fixture(autouse=True)
def isolated_package_environment(
    _package_process_environment: IsolationEvidence,
) -> Iterator[IsolationEvidence]:
    _assert_sentinel(_package_process_environment)
    yield _package_process_environment
    _assert_sentinel(_package_process_environment)
