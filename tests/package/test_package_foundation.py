from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest


ROOT = Path(__file__).parents[2]


def _project() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _run(
    *argv: str, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _archive_candidate(destination: Path) -> None:
    snapshot = destination.parent / f"{destination.name}-index"
    snapshot.mkdir()
    listed = _run(
        "git",
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ).stdout
    for raw in listed.split("\0"):
        if not raw:
            continue
        source = ROOT / raw
        if not source.exists() and not source.is_symlink():
            continue
        if source.is_symlink():
            raise AssertionError(f"candidate contains link: {raw}")
        target = snapshot / raw
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _run("git", "init", "--quiet", cwd=snapshot)
    _run("git", "-c", "core.hooksPath=/dev/null", "add", "--all", cwd=snapshot)
    _run(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.name=Zagrosi Build Test",
        "-c",
        "user.email=build-test@invalid.example",
        "commit",
        "--quiet",
        "-m",
        "build snapshot",
        cwd=snapshot,
    )
    archive_path = destination.parent / f"{destination.name}.tar"
    _run(
        "git",
        "archive",
        "--format=tar",
        f"--output={archive_path}",
        "HEAD",
        cwd=snapshot,
    )

    destination.mkdir()
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            normalized = path.as_posix().casefold()
            assert not path.is_absolute()
            assert path.parts and all(
                part not in {"", ".", ".."} for part in path.parts
            )
            assert normalized not in seen
            seen.add(normalized)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            assert member.isreg(), (
                f"clean archive contains special member: {member.name}"
            )
            source = archive.extractfile(member)
            assert source is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def _wheel_members(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            info.filename: hashlib.sha256(archive.read(info)).hexdigest()
            for info in archive.infolist()
            if not info.is_dir() and not info.filename.endswith("/RECORD")
        }


def _sdist_members(path: Path) -> dict[str, str]:
    members: dict[str, str] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isreg():
                continue
            member_path = PurePosixPath(member.name)
            assert not member_path.is_absolute()
            assert len(member_path.parts) > 1
            assert all(part not in {"", ".", ".."} for part in member_path.parts)
            source = archive.extractfile(member)
            assert source is not None
            relative = PurePosixPath(*member_path.parts[1:]).as_posix()
            assert relative not in members
            members[relative] = hashlib.sha256(source.read()).hexdigest()
    return members


def _toolchain_platform() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in {"amd64", "x86_64"}:
        return "linux-x86_64"
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if sys.platform == "darwin" and machine in {"amd64", "x86_64"}:
        return "macos-x86_64"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    raise AssertionError(f"unsupported package test platform: {sys.platform}/{machine}")


@pytest.fixture(scope="module")
def built_artifacts(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[tuple[Path, Path], tuple[Path, Path]]:
    from zagrosi_forge.install.toolchain import acquire_artifact, load_toolchain_lock

    root = tmp_path_factory.mktemp("package-build")
    wheelhouse = root / "wheelhouse"
    acquire_artifact(
        load_toolchain_lock(),
        tool="uv-build",
        platform=_toolchain_platform(),
        destination=wheelhouse,
        offline=False,
    )
    builds: list[tuple[Path, Path]] = []
    for index in (1, 2):
        source = root / f"source-{index}"
        output = root / f"dist-{index}"
        _archive_candidate(source)
        env = os.environ.copy()
        env.update(
            {
                "UV_CACHE_DIR": str(root / f"uv-cache-{index}"),
                "UV_NO_INDEX": "1",
                "UV_NO_PROGRESS": "1",
                "UV_OFFLINE": "1",
                "UV_PYTHON_DOWNLOADS": "never",
            }
        )
        _run(
            "uv",
            "build",
            "--clear",
            "--no-sources",
            "--offline",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--build-constraints",
            str(source / "build-constraints.txt"),
            "--require-hashes",
            "--sdist",
            "--wheel",
            "--out-dir",
            str(output),
            cwd=source,
            env=env,
        )
        builds.append((next(output.glob("*.whl")), next(output.glob("*.tar.gz"))))
    assert _wheel_members(builds[0][0]) == _wheel_members(builds[1][0])
    assert _sdist_members(builds[0][1]) == _sdist_members(builds[1][1])
    return builds[0], builds[1]


def test_python_support_is_exactly_311_through_314() -> None:
    assert _project()["project"]["requires-python"] == ">=3.11,<3.15"  # type: ignore[index]


def test_package_subprocess_environment_is_isolated(
    isolated_package_environment: tuple[Path, Path, bytes, tuple[int, int]],
) -> None:
    environment_root, sentinel, sentinel_bytes, sentinel_identity = (
        isolated_package_environment
    )
    names = (
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
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "UV_CACHE_DIR",
        "UV_NO_CONFIG",
    )
    observed = json.loads(
        _run(
            sys.executable,
            "-c",
            "import json, os; "
            f"print(json.dumps({{name: os.environ[name] for name in {names!r}}}))",
        ).stdout
    )
    for name in names[:11]:
        assert Path(observed[name]).is_relative_to(environment_root), name
    for name in ("GIT_CONFIG_GLOBAL", "UV_CACHE_DIR"):
        assert Path(observed[name]).is_relative_to(environment_root), name
    assert observed["GIT_CONFIG_NOSYSTEM"] == "1"
    assert observed["UV_NO_CONFIG"] == "1"
    assert not sentinel.is_relative_to(environment_root)
    assert sentinel.read_bytes() == sentinel_bytes
    metadata = sentinel.stat(follow_symlinks=False)
    assert (metadata.st_dev, metadata.st_ino) == sentinel_identity


def test_pyproject_and_checked_in_plugin_versions_match() -> None:
    plugin = json.loads(
        (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    assert _project()["project"]["version"] == plugin["version"]  # type: ignore[index]


def test_uv_lock_is_tracked_and_current() -> None:
    assert (ROOT / "uv.lock").is_file()
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "uv.lock"],
        cwd=ROOT,
        check=False,
    )
    assert ignored.returncode != 0
    _run("uv", "lock", "--check")


def test_build_backend_is_exactly_hash_constrained() -> None:
    project = _project()
    assert project["build-system"] == {
        "requires": ["uv_build==0.11.28"],
        "build-backend": "uv_build",
    }
    constraint = (ROOT / "build-constraints.txt").read_text(encoding="ascii")
    assert constraint.split(maxsplit=1)[0] == "uv_build==0.11.28"
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)", constraint)
    assert len(hashes) == 19
    assert len(set(hashes)) == len(hashes)


def test_clean_archive_builds_wheel_and_sdist_offline(
    built_artifacts: tuple[tuple[Path, Path], tuple[Path, Path]],
) -> None:
    for wheel, sdist in built_artifacts:
        assert wheel.name == "zagrosi_forge-0.2.0-py3-none-any.whl"
        assert sdist.name == "zagrosi_forge-0.2.0.tar.gz"


def test_wheel_imports_trusted_installer_entry_point(
    built_artifacts: tuple[tuple[Path, Path], tuple[Path, Path]],
    tmp_path: Path,
) -> None:
    for index, (wheel, _) in enumerate(built_artifacts, start=1):
        environment = tmp_path / f"venv-{index}"
        env = os.environ.copy()
        env.update(
            {
                "UV_CACHE_DIR": str(tmp_path / f"uv-cache-{index}"),
                "UV_NO_INDEX": "1",
                "UV_OFFLINE": "1",
                "UV_PYTHON_DOWNLOADS": "never",
            }
        )
        _run(
            "uv",
            "venv",
            "--python",
            sys.executable,
            "--no-project",
            str(environment),
            env=env,
        )
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        _run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--offline",
            "--no-index",
            str(wheel),
            env=env,
        )
        result = _run(
            str(python),
            "-I",
            "-c",
            (
                "from zagrosi_forge.install import VERSION, main; "
                "assert VERSION == '0.2.0'; assert callable(main)"
            ),
            cwd=wheel.parent,
            env=env,
        )
        assert result.stdout == ""
        script = environment / (
            "Scripts/zagrosi-forge.exe" if os.name == "nt" else "bin/zagrosi-forge"
        )
        assert (
            _run(str(script), "--version", cwd=wheel.parent, env=env).stdout
            == "0.2.0\n"
        )
        with zipfile.ZipFile(wheel) as archive:
            entry_points = next(
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/entry_points.txt")
            )
            assert (
                "zagrosi-forge = zagrosi_forge.install:main"
                in archive.read(entry_points).decode()
            )


def test_sdist_rebuilds_the_same_normalized_wheel_contents(
    built_artifacts: tuple[tuple[Path, Path], tuple[Path, Path]], tmp_path: Path
) -> None:
    for index, (wheel, sdist) in enumerate(built_artifacts, start=1):
        output = tmp_path / f"rebuilt-{index}"
        env = os.environ.copy()
        env.update(
            {
                "UV_CACHE_DIR": str(tmp_path / f"uv-cache-{index}"),
                "UV_NO_INDEX": "1",
                "UV_NO_PROGRESS": "1",
                "UV_OFFLINE": "1",
                "UV_PYTHON_DOWNLOADS": "never",
            }
        )
        _run(
            "uv",
            "build",
            "--wheel",
            "--offline",
            "--no-index",
            "--find-links",
            str(wheel.parents[1] / "wheelhouse"),
            "--build-constraints",
            str(ROOT / "build-constraints.txt"),
            "--require-hashes",
            "--out-dir",
            str(output),
            str(sdist),
            env=env,
        )
        rebuilt = next(output.glob("*.whl"))
        assert _wheel_members(rebuilt) == _wheel_members(wheel)


def test_python_artifacts_exclude_plugin_only_and_dirty_content(
    built_artifacts: tuple[tuple[Path, Path], tuple[Path, Path]],
) -> None:
    forbidden = (
        "/.git/",
        "/.venv/",
        "/planning/",
        "/skills/",
        "/assets/",
        "/tests/",
        "/scripts/",
        "/__pycache__/",
    )
    for wheel, sdist in built_artifacts:
        with zipfile.ZipFile(wheel) as archive:
            wheel_names = [f"/{name}" for name in archive.namelist()]
        with tarfile.open(sdist, "r:gz") as archive:
            sdist_names = [f"/{member.name}" for member in archive.getmembers()]
        assert not any(token in name for token in forbidden for name in wheel_names)
        assert not any(token in name for token in forbidden for name in sdist_names)
