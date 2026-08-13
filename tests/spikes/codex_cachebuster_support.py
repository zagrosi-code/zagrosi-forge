"""Test-only native Codex cachebuster probe.

The spike deliberately builds a small positive marketplace instead of installing
the repository checkout. It executes only the pinned CLI contract and keeps all
profile, application-data, cache, and temporary paths disposable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import TypeAlias
import zipfile

from zagrosi_forge.install.toolchain import (
    acquire_artifact,
    load_toolchain_lock,
    select_artifact,
)


PINNED_CODEX_VERSION = "0.144.4"
BASE_VERSION = "0.2.0"
MARKETPLACE_NAME = "zagrosi-spike"
PLUGIN_NAME = "zagrosi-forge"
MARKER_TOKEN = b"{{MARKER}}"
MAX_OUTPUT_BYTES = 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 90
# Pinned Linux Codex 0.144.4 expands to about 285 MiB. This test-only tool
# bound is separate from candidate archive limits and never adapts at runtime.
MAX_CODEX_EXECUTABLE_BYTES = 384 * 1024 * 1024

PROFILE_VARIABLES = (
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

PLUGIN_FILES = (
    ".codex-plugin/plugin.json",
    "scripts/spike.py",
    "skills/zagrosi-implement/SKILL.md",
    "skills/zagrosi-plan/SKILL.md",
    "skills/zagrosi-project/SKILL.md",
)

FileManifest: TypeAlias = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CandidateProbe:
    marker: str
    base_version: str
    base_payload_digest: str
    install_version: str
    selected_version: str
    marketplace_name: str
    marketplace_listed: bool
    plugin_available: bool
    plugin_installed: bool
    discovered_skills: tuple[str, ...]
    prompt_marker: str
    cache_marker: str
    rendered_manifest: FileManifest
    cache_manifest: FileManifest
    cache_root: Path
    isolated_root: Path
    isolated_codex_home: Path
    all_profile_variables_isolated: bool
    scripts_field_outcome: str


@dataclass(frozen=True, slots=True)
class CachebusterProbe:
    codex_version: str
    tool_source: str
    locked_artifact_sha256: str
    candidates: tuple[CandidateProbe, CandidateProbe]
    sentinel_bytes_unchanged: bool
    sentinel_identity_unchanged: bool


@dataclass(frozen=True, slots=True)
class _RenderedCandidate:
    marketplace_root: Path
    plugin_root: Path
    base_payload_digest: str
    install_version: str
    rendered_manifest: FileManifest


@dataclass(frozen=True, slots=True)
class _CodexTool:
    executable: Path
    source: str
    locked_artifact_sha256: str


def _regular_tree_manifest(root: Path) -> FileManifest:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("probe tree root is not a real directory")
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise RuntimeError("probe tree contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError("probe tree contains a non-regular member")
        relative = path.relative_to(root).as_posix()
        entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(entries)


def _payload_digest(manifest: FileManifest) -> str:
    value = [{"path": path, "sha256": digest} for path, digest in manifest]
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _render_candidate(
    *, fixture_root: Path, destination: Path, marker: str
) -> _RenderedCandidate:
    source_plugin = fixture_root / "plugins" / PLUGIN_NAME
    actual_files = tuple(
        path for path, _digest in _regular_tree_manifest(source_plugin)
    )
    if actual_files != PLUGIN_FILES:
        raise RuntimeError("fixed spike fixture has unexpected plugin members")

    marketplace_source = fixture_root / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_source.read_bytes())
    expected_source = {
        "source": "local",
        "path": f"./plugins/{PLUGIN_NAME}",
    }
    if (
        marketplace.get("name") != MARKETPLACE_NAME
        or len(marketplace.get("plugins", [])) != 1
        or marketplace["plugins"][0].get("name") != PLUGIN_NAME
        or marketplace["plugins"][0].get("source") != expected_source
    ):
        raise RuntimeError("fixed spike marketplace does not match the generated shape")

    marketplace_target = destination / ".agents" / "plugins" / "marketplace.json"
    marketplace_target.parent.mkdir(parents=True)
    marketplace_target.write_bytes(marketplace_source.read_bytes())

    plugin_target = destination / "plugins" / PLUGIN_NAME
    for relative in PLUGIN_FILES:
        source = source_plugin / relative
        target = plugin_target / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        if relative == "skills/zagrosi-implement/SKILL.md":
            if content.count(MARKER_TOKEN) != 1:
                raise RuntimeError("fixed spike fixture must contain one marker token")
            content = content.replace(MARKER_TOKEN, marker.encode("ascii"))
        elif MARKER_TOKEN in content:
            raise RuntimeError("marker token appears outside its declared skill")
        target.write_bytes(content)

    plugin_manifest_path = plugin_target / ".codex-plugin" / "plugin.json"
    plugin_manifest = json.loads(plugin_manifest_path.read_bytes())
    if (
        plugin_manifest.get("name") != PLUGIN_NAME
        or plugin_manifest.get("version") != BASE_VERSION
        or plugin_manifest.get("skills") != "./skills/"
        or plugin_manifest.get("scripts") != "./scripts/"
    ):
        raise RuntimeError(
            "fixed spike plugin manifest is not the positive source contract"
        )

    base_payload_digest = _payload_digest(_regular_tree_manifest(plugin_target))
    install_version = f"{BASE_VERSION}+codex.local-{base_payload_digest[:32]}"
    plugin_manifest["version"] = install_version
    plugin_manifest_path.write_text(
        json.dumps(plugin_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return _RenderedCandidate(
        marketplace_root=destination,
        plugin_root=plugin_target,
        base_payload_digest=base_payload_digest,
        install_version=install_version,
        rendered_manifest=_regular_tree_manifest(plugin_target),
    )


def _platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "linux" and machine in {"amd64", "x86_64"}:
        return "linux-x86_64"
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if sys.platform == "darwin" and machine in {"amd64", "x86_64"}:
        return "macos-x86_64"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    raise RuntimeError("current platform has no pinned Codex artifact")


def _locked_codex() -> tuple[object, dict[str, str]]:
    lock = load_toolchain_lock()
    tools = lock.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
        raise RuntimeError("packaged toolchain lock has no tools")
    codex_entries = [
        item
        for item in tools
        if isinstance(item, Mapping) and item.get("name") == "codex"
    ]
    if (
        len(codex_entries) != 1
        or codex_entries[0].get("version") != PINNED_CODEX_VERSION
    ):
        raise RuntimeError("packaged toolchain lock does not bind Codex 0.144.4")
    artifact = dict(select_artifact(lock, tool="codex", platform=_platform_key()))
    return lock, artifact


def _extract_locked_codex(
    archive: Path, *, artifact: dict[str, str], destination: Path
) -> Path:
    expected_member = artifact["executable"]
    if (
        not expected_member
        or expected_member.startswith(("/", "\\"))
        or ".." in Path(expected_member).parts
        or "\\" in expected_member
    ):
        raise RuntimeError("locked Codex executable member is unsafe")
    destination.mkdir()
    executable = destination / Path(expected_member).name
    archive_type = artifact["archive_type"]

    def write_stream(stream: object, declared_size: int) -> None:
        if declared_size > MAX_CODEX_EXECUTABLE_BYTES:
            raise RuntimeError(
                "locked Codex archive member exceeds the extraction limit"
            )
        descriptor = os.open(
            executable,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        total = 0
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                while True:
                    chunk = stream.read(1024 * 1024)  # type: ignore[attr-defined]
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_CODEX_EXECUTABLE_BYTES:
                        raise RuntimeError(
                            "locked Codex executable exceeds the extraction limit"
                        )
                    output.write(chunk)
            if total != declared_size:
                raise RuntimeError(
                    "locked Codex executable size does not match archive metadata"
                )
        except BaseException:
            executable.unlink(missing_ok=True)
            raise

    if archive_type == "tar.gz":
        with tarfile.open(archive, mode="r:gz") as source:
            members = source.getmembers()
            if len(members) != 1 or members[0].name != expected_member:
                raise RuntimeError("locked Codex archive has unexpected members")
            member = members[0]
            if not member.isfile():
                raise RuntimeError("locked Codex archive member is not a bounded file")
            stream = source.extractfile(member)
            if stream is None:
                raise RuntimeError("locked Codex archive member cannot be read")
            write_stream(stream, member.size)
    elif archive_type == "zip":
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if len(members) != 1 or members[0].filename != expected_member:
                raise RuntimeError("locked Codex archive has unexpected members")
            member = members[0]
            member_mode = (member.external_attr >> 16) & 0xFFFF
            if member.is_dir() or stat.S_ISLNK(member_mode):
                raise RuntimeError("locked Codex archive member is not a bounded file")
            with source.open(member) as stream:
                write_stream(stream, member.file_size)
    else:
        raise RuntimeError("locked Codex archive type is unsupported")

    if os.name != "nt":
        executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return executable.resolve(strict=True)


def _path_codex_matches(executable: Path, *, work_root: Path) -> bool:
    check_root = work_root / "path-tool-profile"
    check_root.mkdir()
    environment, _roots = _isolated_environment(check_root)
    workspace = check_root / "workspace"
    workspace.mkdir()
    try:
        output = _run(
            executable,
            ("--version",),
            environment=environment,
            cwd=workspace,
        ).strip()
    except RuntimeError:
        return False
    return output == f"codex-cli {PINNED_CODEX_VERSION}"


def _codex_tool(work_root: Path) -> _CodexTool:
    lock, artifact = _locked_codex()
    artifact_sha256 = artifact["sha256"]
    declared = os.environ.get("ZAGROSI_CODEX_BIN")
    if declared:
        executable = Path(declared)
        if not executable.is_absolute() or not executable.is_file():
            raise RuntimeError(
                "ZAGROSI_CODEX_BIN must resolve to an absolute regular file"
            )
        return _CodexTool(executable.resolve(), "declared", artifact_sha256)

    discovered = shutil.which("codex")
    if discovered:
        executable = Path(discovered).resolve()
        if executable.is_file() and _path_codex_matches(
            executable, work_root=work_root
        ):
            return _CodexTool(executable, "path", artifact_sha256)

    artifact_dir = work_root / "codex-artifact"
    archive = acquire_artifact(
        lock,
        tool="codex",
        platform=_platform_key(),
        destination=artifact_dir,
        offline=False,
    )
    executable = _extract_locked_codex(
        archive,
        artifact=artifact,
        destination=work_root / "codex-executable",
    )
    if not executable.is_file():
        raise RuntimeError("ZAGROSI_CODEX_BIN must resolve to an absolute regular file")
    return _CodexTool(executable, "acquired", artifact_sha256)


def _isolated_environment(root: Path) -> tuple[dict[str, str], dict[str, Path]]:
    retained_keys = (
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SystemRoot",
        "WINDIR",
    )
    environment = {key: os.environ[key] for key in retained_keys if key in os.environ}
    environment.update(
        {
            "CI": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "NO_COLOR": "1",
            "RUST_BACKTRACE": "0",
            "TERM": "dumb",
        }
    )

    roots: dict[str, Path] = {}
    for name in PROFILE_VARIABLES:
        path = root / name.lower().replace("_", "-")
        path.mkdir(parents=True)
        roots[name] = path.resolve()
        environment[name] = str(roots[name])
    environment["GIT_CONFIG_GLOBAL"] = str((root / "git-config").resolve())
    return environment, roots


def _run(
    executable: Path,
    arguments: tuple[str, ...],
    *,
    environment: dict[str, str],
    cwd: Path,
) -> str:
    temp_root = environment["TMPDIR"]
    with (
        tempfile.TemporaryFile(dir=temp_root) as stdout,
        tempfile.TemporaryFile(dir=temp_root) as stderr,
    ):
        process = subprocess.Popen(
            [str(executable), *arguments],
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        while process.poll() is None:
            if (
                os.fstat(stdout.fileno()).st_size > MAX_OUTPUT_BYTES
                or os.fstat(stderr.fileno()).st_size > MAX_OUTPUT_BYTES
            ):
                process.kill()
                process.wait()
                raise RuntimeError(
                    "pinned Codex command exceeded the 1 MiB channel limit"
                )
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise RuntimeError(
                    "pinned Codex command exceeded the 90-second spike limit"
                )
            time.sleep(0.01)

        stdout_size = os.fstat(stdout.fileno()).st_size
        stderr_size = os.fstat(stderr.fileno()).st_size
        if stdout_size > MAX_OUTPUT_BYTES or stderr_size > MAX_OUTPUT_BYTES:
            raise RuntimeError("pinned Codex command exceeded the 1 MiB channel limit")
        stdout.seek(0)
        stderr.seek(0)
        stdout_bytes = stdout.read(MAX_OUTPUT_BYTES + 1)
        stderr_bytes = stderr.read(MAX_OUTPUT_BYTES + 1)

    if process.returncode != 0:
        tail = stderr_bytes[-16_384:].decode("utf-8", errors="replace")
        tail = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "?", tail)
        raise RuntimeError(
            f"pinned Codex command failed ({process.returncode}): {tail}"
        )
    return stdout_bytes.decode("utf-8", errors="strict")


def _run_json(
    executable: Path,
    arguments: tuple[str, ...],
    *,
    environment: dict[str, str],
    cwd: Path,
) -> object:
    output = _run(executable, arguments, environment=environment, cwd=cwd)
    try:
        return json.loads(output)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("pinned Codex emitted malformed JSON") from error


def _object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"pinned Codex {label} output is not an object")
    return value


def _object_list(value: object, *, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RuntimeError(f"pinned Codex {label} output is not an object list")
    return value


def _prompt_skill_catalog(value: object) -> dict[str, str]:
    texts: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if key == "text" and isinstance(nested, str):
                    texts.append(nested)
                else:
                    collect(nested)
        elif isinstance(item, list):
            for nested in item:
                collect(nested)

    collect(value)
    pattern = re.compile(r"^- zagrosi-forge:([a-z0-9-]+): ([^\n]+)$", re.MULTILINE)
    entries: dict[str, str] = {}
    for name, description in pattern.findall("\n".join(texts)):
        if name in entries:
            raise RuntimeError("pinned Codex prompt catalog repeats a Forge skill")
        entries[name] = description
    return entries


def _probe_candidate(
    *,
    executable: Path,
    work_root: Path,
    fixture_root: Path,
    marker: str,
    isolated_root: Path,
    environment: dict[str, str],
    profile_roots: dict[str, Path],
    workspace: Path,
    replace_marketplace: bool,
) -> CandidateProbe:
    candidate_root = work_root / f"candidate-{marker.lower()}"
    rendered = _render_candidate(
        fixture_root=fixture_root,
        destination=candidate_root,
        marker=marker,
    )
    if replace_marketplace:
        _object(
            _run_json(
                executable,
                (
                    "plugin",
                    "marketplace",
                    "remove",
                    MARKETPLACE_NAME,
                    "--json",
                ),
                environment=environment,
                cwd=workspace,
            ),
            label="marketplace remove",
        )

    added = _object(
        _run_json(
            executable,
            ("plugin", "marketplace", "add", str(rendered.marketplace_root), "--json"),
            environment=environment,
            cwd=workspace,
        ),
        label="marketplace add",
    )
    if added.get("marketplaceName") != MARKETPLACE_NAME:
        raise RuntimeError("pinned Codex selected the wrong marketplace")

    marketplaces = _object(
        _run_json(
            executable,
            ("plugin", "marketplace", "list", "--json"),
            environment=environment,
            cwd=workspace,
        ),
        label="marketplace list",
    )
    marketplace_items = _object_list(
        marketplaces.get("marketplaces"), label="marketplace list entries"
    )
    matching_marketplaces = [
        item for item in marketplace_items if item.get("name") == MARKETPLACE_NAME
    ]
    marketplace_listed = len(matching_marketplaces) == 1

    available = _object(
        _run_json(
            executable,
            ("plugin", "list", "--available", "--json"),
            environment=environment,
            cwd=workspace,
        ),
        label="available plugin list",
    )
    available_items = _object_list(
        available.get("available"), label="available plugin entries"
    )
    catalogued_installed_items = _object_list(
        available.get("installed"), label="catalogued installed plugin entries"
    )
    matching_available = [
        item
        for item in [*available_items, *catalogued_installed_items]
        if item.get("name") == PLUGIN_NAME
        and item.get("marketplaceName") == MARKETPLACE_NAME
    ]
    plugin_available = len(matching_available) == 1

    installed_result = _object(
        _run_json(
            executable,
            ("plugin", "add", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}", "--json"),
            environment=environment,
            cwd=workspace,
        ),
        label="plugin add",
    )
    selected_version = installed_result.get("version")
    installed_path = installed_result.get("installedPath")
    if not isinstance(selected_version, str) or not isinstance(installed_path, str):
        raise RuntimeError("pinned Codex plugin add omitted selected identity")

    cache_root = Path(installed_path).resolve(strict=True)
    codex_home = profile_roots["CODEX_HOME"]
    if not cache_root.is_relative_to(codex_home):
        raise RuntimeError("pinned Codex selected cache outside isolated CODEX_HOME")

    installed = _object(
        _run_json(
            executable,
            ("plugin", "list", "--json"),
            environment=environment,
            cwd=workspace,
        ),
        label="installed plugin list",
    )
    installed_items = _object_list(
        installed.get("installed"), label="installed plugin entries"
    )
    matching_installed = [
        item
        for item in installed_items
        if item.get("name") == PLUGIN_NAME
        and item.get("marketplaceName") == MARKETPLACE_NAME
        and item.get("version") == rendered.install_version
        and item.get("installed") is True
        and item.get("enabled") is True
    ]
    plugin_installed = len(matching_installed) == 1

    prompt_input = _run_json(
        executable,
        ("debug", "prompt-input"),
        environment=environment,
        cwd=workspace,
    )
    catalog = _prompt_skill_catalog(prompt_input)
    marker_match = re.search(
        r"Cachebuster probe marker ([AB]) for Forge implementation discovery\.",
        catalog.get("zagrosi-implement", ""),
    )
    if marker_match is None:
        raise RuntimeError("pinned Codex prompt catalog omitted the candidate marker")

    cached_marker_content = (
        cache_root / "skills" / "zagrosi-implement" / "SKILL.md"
    ).read_text(encoding="utf-8")
    cached_marker = re.search(
        r"Cachebuster probe marker ([AB]) ", cached_marker_content
    )
    if cached_marker is None:
        raise RuntimeError("pinned Codex cache omitted the candidate marker")

    source_manifest = json.loads(
        (rendered.plugin_root / ".codex-plugin" / "plugin.json").read_bytes()
    )
    if source_manifest.get("scripts") != "./scripts/":
        raise RuntimeError("rendered candidate lost the scripts-field probe")
    scripts_field_outcome = "accepted_no_execution_observed"
    profile_isolated = all(
        Path(environment[name]).resolve().is_relative_to(isolated_root)
        for name in PROFILE_VARIABLES
    )
    return CandidateProbe(
        marker=marker,
        base_version=BASE_VERSION,
        base_payload_digest=rendered.base_payload_digest,
        install_version=rendered.install_version,
        selected_version=selected_version,
        marketplace_name=MARKETPLACE_NAME,
        marketplace_listed=marketplace_listed,
        plugin_available=plugin_available,
        plugin_installed=plugin_installed,
        discovered_skills=tuple(sorted(catalog)),
        prompt_marker=marker_match.group(1),
        cache_marker=cached_marker.group(1),
        rendered_manifest=rendered.rendered_manifest,
        cache_manifest=_regular_tree_manifest(cache_root),
        cache_root=cache_root,
        isolated_root=isolated_root,
        isolated_codex_home=codex_home,
        all_profile_variables_isolated=profile_isolated,
        scripts_field_outcome=scripts_field_outcome,
    )


def run_cachebuster_probe(*, work_root: Path, fixture_root: Path) -> CachebusterProbe:
    """Run the A/B native probe without addressing any real profile path."""

    work_root = work_root.resolve()
    fixture_root = fixture_root.resolve(strict=True)
    tool = _codex_tool(work_root)
    executable = tool.executable

    external_home = work_root / "external-home"
    external_home.mkdir()
    sentinel = external_home / "do-not-touch.sentinel"
    sentinel.write_bytes(b"zagrosi-cachebuster-sentinel-v1\n")
    initial_bytes = sentinel.read_bytes()
    initial_stat = sentinel.stat()
    initial_identity = (
        initial_stat.st_dev,
        initial_stat.st_ino,
        initial_stat.st_mode,
        initial_stat.st_size,
        initial_stat.st_mtime_ns,
    )

    isolated_root = (work_root / "isolated-profile").resolve()
    isolated_root.mkdir()
    environment, profile_roots = _isolated_environment(isolated_root)
    workspace = isolated_root / "workspace"
    workspace.mkdir()
    version_output = _run(
        executable,
        ("--version",),
        environment=environment,
        cwd=workspace,
    ).strip()
    if version_output != f"codex-cli {PINNED_CODEX_VERSION}":
        raise RuntimeError(
            f"pinned Codex version mismatch: expected {PINNED_CODEX_VERSION}"
        )

    candidates = (
        _probe_candidate(
            executable=executable,
            work_root=work_root,
            fixture_root=fixture_root,
            marker="A",
            isolated_root=isolated_root,
            environment=environment,
            profile_roots=profile_roots,
            workspace=workspace,
            replace_marketplace=False,
        ),
        _probe_candidate(
            executable=executable,
            work_root=work_root,
            fixture_root=fixture_root,
            marker="B",
            isolated_root=isolated_root,
            environment=environment,
            profile_roots=profile_roots,
            workspace=workspace,
            replace_marketplace=True,
        ),
    )

    final_bytes = sentinel.read_bytes()
    final_stat = sentinel.stat()
    final_identity = (
        final_stat.st_dev,
        final_stat.st_ino,
        final_stat.st_mode,
        final_stat.st_size,
        final_stat.st_mtime_ns,
    )
    return CachebusterProbe(
        codex_version=PINNED_CODEX_VERSION,
        tool_source=tool.source,
        locked_artifact_sha256=tool.locked_artifact_sha256,
        candidates=candidates,
        sentinel_bytes_unchanged=final_bytes == initial_bytes,
        sentinel_identity_unchanged=final_identity == initial_identity,
    )
