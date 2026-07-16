from __future__ import annotations

from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass, replace
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
from typing import Any, Iterator
import warnings
import zipfile

import pytest


ROOT = Path(__file__).parents[2]
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_SOURCE_DATE_EPOCH = "315532800"
GENERATED_MANIFEST_PATH = ".codex-plugin/bundle-manifest.json"
DIRTY_BUILD_CANARY = b"ZAGROSI_DIRTY_BUILD_CANARY_7f6a3e2d"


def _api() -> Any:
    import zagrosi_forge.install.artifacts as artifacts

    return artifacts


def _bundle_policy() -> Any:
    from zagrosi_forge.install.bundle import load_trusted_bundle_policy

    return load_trusted_bundle_policy()


def _python_policy() -> Any:
    return _api().derive_python_artifact_policy(_bundle_policy())


@dataclass(frozen=True)
class _PluginAuthority:
    bundle: object
    policy: object


@pytest.fixture(scope="module")
def plugin_authority(tmp_path_factory: pytest.TempPathFactory) -> _PluginAuthority:
    from tests.package.test_bundle_contract import _base_bundle
    import zagrosi_forge.install.bundle as bundle_api

    root = tmp_path_factory.mktemp("canonical-plugin")
    return _PluginAuthority(
        bundle=_base_bundle(root / "candidate"),
        policy=bundle_api.load_trusted_bundle_policy(),
    )


def _expected_plugin_paths(authority: _PluginAuthority) -> tuple[str, ...]:
    paths = tuple(entry.path for entry in authority.bundle.manifest.entries)  # type: ignore[attr-defined]
    return tuple(sorted((*paths, GENERATED_MANIFEST_PATH)))


def _payload_manifest(
    authority: _PluginAuthority,
    members: tuple[tuple[str, bytes, int], ...],
) -> bytes:
    from zagrosi_forge.install.bundle import canonical_bundle_json_bytes

    entries = [
        {
            "file_type": "regular",
            "mode": mode,
            "path": path,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
        for path, data, mode in members
    ]
    entries.sort(key=lambda item: item["path"])
    manifest = authority.bundle.manifest  # type: ignore[attr-defined]
    domain = {
        "aggregate_size": sum(entry["size"] for entry in entries),
        "base_version": manifest.base_version,
        "entries": entries,
        "normalization_profile": manifest.normalization_profile,
        "policy_digest": manifest.policy_digest,
        "schema_version": manifest.schema_version,
    }
    return canonical_bundle_json_bytes(
        {
            **domain,
            "builder_version": manifest.builder_version,
            "payload_digest": hashlib.sha256(
                canonical_bundle_json_bytes(domain)
            ).hexdigest(),
        },
        final_newline=True,
    )


def _plugin_member_tuples(
    authority: _PluginAuthority,
) -> tuple[tuple[str, bytes, int], ...]:
    bundle = authority.bundle
    return tuple(
        sorted(
            (
                *(
                    (entry.path, bundle.entry_bytes[entry.path], entry.mode)
                    for entry in bundle.manifest.entries
                ),
                (GENERATED_MANIFEST_PATH, bundle.manifest_bytes, 0o644),
            ),
            key=lambda item: item[0],
        )
    )


def _limits(
    *,
    members: int = 128,
    compressed: int = 2 * 1024 * 1024,
    expanded: int = 4 * 1024 * 1024,
    ratio: int = 20,
) -> object:
    return _api().ArchiveLimits(
        max_members=members,
        max_compressed_bytes=compressed,
        max_expanded_bytes=expanded,
        max_ratio=ratio,
    )


def _inspect_plugin(
    raw: bytes,
    *,
    authority: _PluginAuthority,
    limits: object | None = None,
) -> object:
    api = _api()
    return api.inspect_plugin_zip(
        raw,
        expected=authority.bundle,
        policy=authority.policy,
        limits=_limits() if limits is None else limits,
    )


def _failure_code(call: Any) -> str:
    from zagrosi_forge.install.contracts import ForgeError

    with pytest.raises(ForgeError) as caught:
        call()
    assert caught.value.exit_category == 12
    return caught.value.code


def _raw_zip(
    members: tuple[tuple[str, bytes, int], ...],
    *,
    timestamp: tuple[int, int, int, int, int, int] = FIXED_ZIP_TIME,
    comment: bytes = b"",
    compresslevel: int = 9,
) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compresslevel,
        ) as archive:
            archive.comment = comment
            for path, data, mode in members:
                info = zipfile.ZipInfo(path, date_time=timestamp)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (mode & 0xFFFF) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _raw_zip_with_data_descriptors(
    members: tuple[tuple[str, bytes, int], ...],
) -> bytes:
    class UnseekableBuffer(io.BytesIO):
        def seekable(self) -> bool:
            return False

        def seek(self, *_args: object, **_kwargs: object) -> int:
            raise io.UnsupportedOperation("unseekable")

    output = UnseekableBuffer()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path, data, mode in members:
            info = zipfile.ZipInfo(path, date_time=FIXED_ZIP_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (mode & 0xFFFF) << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def _raw_empty_directory_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("empty/", date_time=FIXED_ZIP_TIME)
        info.create_system = 3
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
        archive.writestr(info, b"")
    return output.getvalue()


def _central_record_offset(raw: bytes, filename: str) -> int:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        offset = archive.start_dir
        for info in archive.infolist():
            if info.filename == filename:
                return offset
            filename_size = int.from_bytes(raw[offset + 28 : offset + 30], "little")
            extra_size = int.from_bytes(raw[offset + 30 : offset + 32], "little")
            comment_size = int.from_bytes(raw[offset + 32 : offset + 34], "little")
            offset += 46 + filename_size + extra_size + comment_size
    raise AssertionError(filename)


def _zip_framing_failure_code(raw: bytes) -> str:
    api = _api()

    def validate() -> None:
        central_offset, eocd_offset, entry_count = api._validate_zip_framing(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            api._validate_zip_member_framing(
                raw, archive, central_offset, eocd_offset, entry_count
            )

    return _failure_code(validate)


def _deterministic_bytes(size: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    total = 0
    while total < size:
        chunks.append(hashlib.sha256(f"artifact-{offset}".encode()).digest())
        total += len(chunks[-1])
        offset += 1
    return b"".join(chunks)[:size]


@contextmanager
def _archive_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timezone: str,
    locale: str,
    mask: int,
) -> Iterator[None]:
    monkeypatch.setenv("TZ", timezone)
    monkeypatch.setenv("LC_ALL", locale)
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _member_map(evidence: object) -> dict[str, object]:
    return {member.path: member for member in evidence.members}  # type: ignore[attr-defined]


def test_controlled_plugin_zip_is_byte_identical_twice(
    plugin_authority: _PluginAuthority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    with _archive_environment(
        monkeypatch,
        timezone="Pacific/Kiritimati",
        locale="C",
        mask=0o077,
    ):
        first = api.write_controlled_plugin_zip(
            plugin_authority.bundle, plugin_authority.policy
        )
    with _archive_environment(
        monkeypatch,
        timezone="America/Adak",
        locale="C.UTF-8",
        mask=0o022,
    ):
        second = api.write_controlled_plugin_zip(
            plugin_authority.bundle, plugin_authority.policy
        )

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_controlled_plugin_zip_rejects_noncanonical_container_framing(
    plugin_authority: _PluginAuthority,
) -> None:
    controlled = _api().write_controlled_plugin_zip(
        plugin_authority.bundle, plugin_authority.policy
    )
    members = _plugin_member_tuples(plugin_authority)
    assert (
        _failure_code(
            lambda: _inspect_plugin(
                b"SHELL-STUB\n" + controlled, authority=plugin_authority
            )
        )
        == "artifact.unsafe_member"
    )
    assert (
        _failure_code(
            lambda: _inspect_plugin(
                b"PK\x03\x04SFX-STUB" + controlled,
                authority=plugin_authority,
            )
        )
        == "artifact.unsafe_member"
    )
    assert (
        _failure_code(
            lambda: _inspect_plugin(
                controlled + b"TRAILING-STUB", authority=plugin_authority
            )
        )
        == "artifact.unsafe_member"
    )
    for changed in (
        _raw_zip(members, compresslevel=1),
        _raw_zip_with_data_descriptors(members),
    ):
        assert (
            _failure_code(
                lambda changed=changed: _inspect_plugin(
                    changed, authority=plugin_authority
                )
            )
            == "artifact.manifest_mismatch"
        )


def test_zip_framing_rejects_noncanonical_directory_crc() -> None:
    raw = bytearray(_raw_empty_directory_zip())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        local_offset = archive.infolist()[0].header_offset
    central_offset = _central_record_offset(bytes(raw), "empty/")
    raw[local_offset + 14 : local_offset + 18] = (1).to_bytes(4, "little")
    raw[central_offset + 16 : central_offset + 20] = (1).to_bytes(4, "little")

    assert _zip_framing_failure_code(bytes(raw)) == "artifact.unsafe_member"


@pytest.mark.parametrize("field_offset", (14, 18, 22))
def test_zip_framing_rejects_nonzero_data_descriptor_placeholders(
    field_offset: int,
) -> None:
    raw = bytearray(
        _raw_zip_with_data_descriptors((("payload", b"payload", 0o100644),))
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
    assert info.flag_bits & 0x0008
    start = info.header_offset + field_offset
    raw[start : start + 4] = (1).to_bytes(4, "little")

    assert _zip_framing_failure_code(bytes(raw)) == "artifact.unsafe_member"


@pytest.mark.parametrize("case", ("ratio", "member"))
def test_sdist_framing_enforces_limits_with_bounded_output(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api()
    if case == "ratio":
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            item = tarfile.TarInfo("root/payload")
            payload = b"x" * (2 * 1024 * 1024)
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
        raw = output.getvalue()
    else:
        item = tarfile.TarInfo("root/oversized")
        item.size = api.LIMIT_POLICY.value("bundle_member_bytes") + 1
        padding = (-item.size) % tarfile.BLOCKSIZE
        raw = gzip.compress(
            item.tobuf()
            + b"x" * item.size
            + tarfile.NUL * (padding + 2 * tarfile.BLOCKSIZE),
            mtime=0,
        )
    fully_expanded = len(gzip.decompress(raw))
    observed: list[int] = []
    original = api.zlib.decompressobj

    class ObservedDecompressor:
        def __init__(self, inner: object) -> None:
            self.inner = inner

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            assert 0 < max_length <= 64 * 1024
            expanded = self.inner.decompress(data, max_length)  # type: ignore[attr-defined]
            observed.append(len(expanded))
            return expanded

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    monkeypatch.setattr(
        api.zlib,
        "decompressobj",
        lambda *args, **kwargs: ObservedDecompressor(original(*args, **kwargs)),
    )

    assert _failure_code(lambda: api._validate_tar_framing(raw)) == (
        "bundle.limit_exceeded"
    )
    assert observed and max(observed) <= 64 * 1024
    assert sum(observed) < fully_expanded


def test_archive_metadata_has_fixed_time_owner_order_mode_and_compression(
    plugin_authority: _PluginAuthority,
) -> None:
    raw = _api().write_controlled_plugin_zip(
        plugin_authority.bundle, plugin_authority.policy
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()

    expected_paths = _expected_plugin_paths(plugin_authority)
    executables = frozenset(plugin_authority.policy.executable_files)  # type: ignore[attr-defined]
    assert [info.filename for info in infos] == list(expected_paths)
    assert all(info.date_time == FIXED_ZIP_TIME for info in infos)
    assert all(info.create_system == 3 for info in infos)
    assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in infos)
    assert all(info.extra == b"" and info.comment == b"" for info in infos)
    assert all(not info.filename.endswith("/") for info in infos)
    modes = {info.filename: (info.external_attr >> 16) & 0o777 for info in infos}
    assert modes == {
        path: 0o755 if path in executables else 0o644 for path in expected_paths
    }


def test_archive_inspector_rejects_traversal_duplicates_links_and_zip_bomb(
    plugin_authority: _PluginAuthority,
) -> None:
    duplicate = _raw_zip(
        (("README.md", b"one", 0o100644), ("README.md", b"two", 0o100644))
    )
    traversal = _raw_zip((("../outside", b"secret", 0o100644),))
    link = _raw_zip((("linked", b"outside", stat.S_IFLNK | 0o777),))
    bomb = _raw_zip((("large.bin", b"0" * (256 * 1024), 0o100644),))

    for raw in (duplicate, traversal, link):
        assert _failure_code(
            lambda raw=raw: _inspect_plugin(raw, authority=plugin_authority)
        ) == ("artifact.unsafe_member")
    assert (
        _failure_code(
            lambda: _inspect_plugin(
                bomb,
                authority=plugin_authority,
                limits=_limits(compressed=len(bomb) + 1, expanded=512 * 1024, ratio=2),
            )
        )
        == "bundle.limit_exceeded"
    )


def test_real_canonical_bundle_round_trips_through_controlled_zip(
    plugin_authority: _PluginAuthority,
) -> None:
    raw = _api().write_controlled_plugin_zip(
        plugin_authority.bundle, plugin_authority.policy
    )
    evidence = _inspect_plugin(raw, authority=plugin_authority)
    members = _member_map(evidence)

    assert tuple(members) == _expected_plugin_paths(plugin_authority)
    assert len(members) == len(plugin_authority.policy.required_files) + 1  # type: ignore[attr-defined]
    assert evidence.manifest_path == GENERATED_MANIFEST_PATH
    manifest = json.loads(plugin_authority.bundle.manifest_bytes)  # type: ignore[attr-defined]
    assert GENERATED_MANIFEST_PATH not in {
        entry["path"] for entry in manifest["entries"]
    }
    assert evidence.normalized_manifest_digest == (  # type: ignore[attr-defined]
        plugin_authority.bundle.manifest.payload_digest  # type: ignore[attr-defined]
    )


def test_archive_rejects_absolute_windows_device_and_unicode_collision_members(
    plugin_authority: _PluginAuthority,
) -> None:
    hostile = (
        (("/absolute", b"x", 0o100644),),
        (("C:/absolute", b"x", 0o100644),),
        ((r"\\server\share\secret", b"x", 0o100644),),
        (("skills/CON/readme.md", b"x", 0o100644),),
        (
            ("skills/caf\u00e9/SKILL.md", b"one", 0o100644),
            ("skills/cafe\u0301/SKILL.md", b"two", 0o100644),
        ),
    )
    for members in hostile:
        raw = _raw_zip(members)
        assert _failure_code(
            lambda raw=raw: _inspect_plugin(raw, authority=plugin_authority)
        ) == ("artifact.unsafe_member")


def test_archive_rejects_duplicate_names_before_extraction(
    plugin_authority: _PluginAuthority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_zip(
        (("README.md", b"first", 0o100644), ("README.md", b"second", 0o100644))
    )
    opened = False

    def forbidden_open(*_args: object, **_kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("duplicate member data was opened")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden_open)
    assert (
        _failure_code(lambda: _inspect_plugin(raw, authority=plugin_authority))
        == "artifact.unsafe_member"
    )
    assert not opened


def test_archive_size_and_ratio_limits_are_enforced_streamingly(
    plugin_authority: _PluginAuthority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    valid = api.write_controlled_plugin_zip(
        plugin_authority.bundle, plugin_authority.policy
    )
    original_read = zipfile.ZipExtFile.read
    original_readinto = zipfile.ZipExtFile.readinto
    largest_request = 0

    def bounded_read(stream: zipfile.ZipExtFile, size: int = -1) -> bytes:
        nonlocal largest_request
        assert 0 <= size <= 64 * 1024
        largest_request = max(largest_request, size)
        return original_read(stream, size)

    def bounded_readinto(stream: zipfile.ZipExtFile, buffer: Any) -> int | None:
        nonlocal largest_request
        assert len(buffer) <= 64 * 1024
        largest_request = max(largest_request, len(buffer))
        return original_readinto(stream, buffer)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", bounded_read)
    monkeypatch.setattr(zipfile.ZipExtFile, "readinto", bounded_readinto)
    _inspect_plugin(valid, authority=plugin_authority)
    assert largest_request > 0

    expanded = _raw_zip((("large.bin", _deterministic_bytes(96 * 1024), 0o100644),))
    assert (
        _failure_code(
            lambda: _inspect_plugin(
                expanded,
                authority=plugin_authority,
                limits=_limits(
                    compressed=len(expanded) + 1,
                    expanded=64 * 1024,
                    ratio=20,
                ),
            )
        )
        == "bundle.limit_exceeded"
    )
    ratio_bomb = _raw_zip((("ratio.bin", b"x" * (128 * 1024), 0o100644),))
    assert (
        _failure_code(
            lambda: _inspect_plugin(
                ratio_bomb,
                authority=plugin_authority,
                limits=_limits(
                    compressed=len(ratio_bomb) + 1,
                    expanded=256 * 1024,
                    ratio=2,
                ),
            )
        )
        == "bundle.limit_exceeded"
    )


def test_archive_member_limit_is_rejected_before_read(
    plugin_authority: _PluginAuthority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_zip((("large.bin", b"123456789", 0o100644),))
    opened = False

    def forbidden_open(*_args: object, **_kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("oversized member data was opened")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden_open)
    limits = _api().ArchiveLimits(
        max_members=2,
        max_compressed_bytes=len(raw) + 1,
        max_expanded_bytes=32,
        max_ratio=20,
        max_member_bytes=8,
    )

    assert (
        _failure_code(
            lambda: _inspect_plugin(raw, authority=plugin_authority, limits=limits)
        )
        == "bundle.limit_exceeded"
    )
    assert not opened


def test_zip_limits_are_enforced_before_archive_parser(
    plugin_authority: _PluginAuthority,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    controlled = api.write_controlled_plugin_zip(
        plugin_authority.bundle, plugin_authority.policy
    )
    with zipfile.ZipFile(io.BytesIO(controlled)) as archive:
        member_count = len(archive.infolist())
    two_member_wheel = _raw_zip(
        (
            ("first", b"1", stat.S_IFREG | 0o644),
            ("second", b"2", stat.S_IFREG | 0o644),
        )
    )
    installed = api._default_archive_limits()

    def forbidden_parser(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ZipFile parser was invoked")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_parser)
    compressed_limit = api.ArchiveLimits(
        max_members=installed.max_members,
        max_compressed_bytes=len(controlled) - 1,
        max_expanded_bytes=installed.max_expanded_bytes,
        max_ratio=installed.max_ratio,
        max_member_bytes=installed.max_member_bytes,
    )
    assert (
        _failure_code(
            lambda: api.inspect_plugin_zip(
                controlled,
                expected=plugin_authority.bundle,
                policy=plugin_authority.policy,
                limits=compressed_limit,
            )
        )
        == "bundle.limit_exceeded"
    )
    member_limit = api.ArchiveLimits(
        max_members=member_count - 1,
        max_compressed_bytes=installed.max_compressed_bytes,
        max_expanded_bytes=installed.max_expanded_bytes,
        max_ratio=installed.max_ratio,
        max_member_bytes=installed.max_member_bytes,
    )
    assert (
        _failure_code(
            lambda: api.inspect_plugin_zip(
                controlled,
                expected=plugin_authority.bundle,
                policy=plugin_authority.policy,
                limits=member_limit,
            )
        )
        == "bundle.limit_exceeded"
    )
    wheel_limit = api.ArchiveLimits(
        max_members=1,
        max_compressed_bytes=installed.max_compressed_bytes,
        max_expanded_bytes=installed.max_expanded_bytes,
        max_ratio=installed.max_ratio,
        max_member_bytes=installed.max_member_bytes,
    )
    monkeypatch.setattr(api, "_default_archive_limits", lambda: wheel_limit)
    assert (
        _failure_code(lambda: _inspect_python(two_member_wheel, kind="wheel"))
        == "bundle.limit_exceeded"
    )


def test_archive_limit_overrides_cannot_relax_installed_policy() -> None:
    api = _api()
    installed = api._default_archive_limits()
    with pytest.raises(ValueError, match="exceeds installed policy"):
        api.ArchiveLimits(
            max_members=installed.max_members + 1,
            max_compressed_bytes=installed.max_compressed_bytes,
            max_expanded_bytes=installed.max_expanded_bytes,
            max_ratio=installed.max_ratio,
            max_member_bytes=installed.max_member_bytes,
        )


def test_plugin_zip_exercises_direct_adapter_and_three_skills(
    plugin_authority: _PluginAuthority,
) -> None:
    evidence = _inspect_plugin(
        _api().write_controlled_plugin_zip(
            plugin_authority.bundle, plugin_authority.policy
        ),
        authority=plugin_authority,
    )
    paths = frozenset(_member_map(evidence))
    executables = frozenset(plugin_authority.policy.executable_files)  # type: ignore[attr-defined]
    skills = frozenset(  # type: ignore[attr-defined]
        plugin_authority.policy.required_conditions["skill_entrypoints"]
    )

    assert paths & executables == executables
    assert len(skills) == 3
    assert {
        path
        for path in paths
        if path.startswith("skills/") and path.endswith("SKILL.md")
    } == skills


def test_candidate_manifest_graph_has_no_digest_cycle(
    plugin_authority: _PluginAuthority,
) -> None:
    members = _plugin_member_tuples(plugin_authority)
    bad_manifest = _payload_manifest(plugin_authority, members)
    changed = tuple(
        (path, bad_manifest if path == GENERATED_MANIFEST_PATH else data, mode)
        for path, data, mode in members
    )

    assert (
        _failure_code(
            lambda: _inspect_plugin(_raw_zip(changed), authority=plugin_authority)
        )
        == "bundle.digest_mismatch"
    )


def test_forged_self_consistent_entry_authority_cannot_mint_evidence(
    plugin_authority: _PluginAuthority,
) -> None:
    raw = _raw_zip(_plugin_member_tuples(plugin_authority))

    assert (
        _failure_code(
            lambda: _api().inspect_plugin_zip(
                raw,
                expected=_plugin_member_tuples(plugin_authority),
                policy=plugin_authority.policy,
                limits=_limits(),
            )
        )
        == "bundle.policy_invalid"
    )


def _run(
    *argv: str,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
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
    raise AssertionError(
        f"unsupported artifact test platform: {sys.platform}/{machine}"
    )


def _safe_extract_source_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir()
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            assert not path.is_absolute()
            assert path.parts and all(
                part not in {"", ".", ".."} for part in path.parts
            )
            collision_key = path.as_posix().casefold()
            assert collision_key not in seen
            seen.add(collision_key)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            assert member.isreg(), f"source archive contains {member.name}"
            source = archive.extractfile(member)
            assert source is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def _clean_archive_source(destination: Path, *, reverse: bool, dirty: bool) -> None:
    snapshot = destination.parent / f"{destination.name}-index"
    snapshot.mkdir()
    names = [
        name
        for name in _run(
            "git",
            "ls-files",
            "--cached",
            "-z",
        ).stdout.split("\0")
        if name
    ]
    for name in sorted(names, reverse=reverse):
        source = ROOT / name
        if not source.exists() and not source.is_symlink():
            continue
        assert not source.is_symlink(), f"candidate contains link: {name}"
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    _run("git", "init", "--quiet", cwd=snapshot)
    _run("git", "-c", "core.hooksPath=/dev/null", "add", "--all", cwd=snapshot)
    _run(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.name=Zagrosi Artifact Test",
        "-c",
        "user.email=artifact-test@invalid.example",
        "commit",
        "--quiet",
        "-m",
        "artifact snapshot",
        cwd=snapshot,
    )
    if dirty:
        for relative in (
            ".git/secret-canary",
            ".venv/secret-canary",
            "planning/secret-canary",
            "tests/secret-canary",
            "src/zagrosi_forge/install/secret-canary.py",
        ):
            canary = snapshot / relative
            canary.parent.mkdir(parents=True, exist_ok=True)
            canary.write_bytes(DIRTY_BUILD_CANARY)
    source_archive = destination.parent / f"{destination.name}.tar"
    _run(
        "git",
        "archive",
        "--format=tar",
        f"--output={source_archive}",
        "HEAD",
        cwd=snapshot,
    )
    _safe_extract_source_archive(source_archive, destination)


def _canonical_bundle(source_root: Path) -> object:
    from tests.package.test_bundle_contract import _validated_package
    import zagrosi_forge.install.bundle as bundle_api
    from zagrosi_forge.install.paths import PlatformPathAuthority

    package = _validated_package(source_root)
    authority = PlatformPathAuthority()
    policy = bundle_api.load_trusted_bundle_policy()
    try:
        with authority.open_source_root(source_root) as source:
            with bundle_api.open_bundle_snapshot(source, policy) as snapshot:
                return bundle_api.enumerate_base_bundle(package, snapshot, policy)
    finally:
        package.source_snapshot.close()


def _build_environment(root: Path, *, index: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C" if index == 1 else "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": FIXED_SOURCE_DATE_EPOCH,
            "TZ": "UTC" if index == 1 else "Etc/GMT+11",
            "UV_CACHE_DIR": str(root / f"uv-cache-{index}"),
            "UV_NO_INDEX": "1",
            "UV_NO_PROGRESS": "1",
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        environment[name] = "http://127.0.0.1:9"
    environment["NO_PROXY"] = ""
    return environment


@dataclass(frozen=True)
class _PythonBuilds:
    first: object
    second: object
    rebuilt: object
    uv_bytes: bytes
    uv_sha256: str
    backend_bytes: bytes
    backend_sha256: str
    constraints_bytes: bytes
    constraints_sha256: str
    source_roots: tuple[Path, Path]
    bundles: tuple[object, object]
    wheelhouse: Path
    constraints_path: Path


@pytest.fixture(scope="module")
def python_builds(tmp_path_factory: pytest.TempPathFactory) -> _PythonBuilds:
    api = _api()
    from zagrosi_forge.install.toolchain import (
        acquire_artifact,
        load_toolchain_lock,
        select_artifact,
    )

    root = tmp_path_factory.mktemp("normalized-python-artifacts")
    wheelhouse = root / "wheelhouse"
    lock = load_toolchain_lock()
    backend_locked = select_artifact(
        lock,
        tool="uv-build",
        platform=_toolchain_platform(),
    )
    uv_locked = select_artifact(
        lock,
        tool="uv",
        platform=_toolchain_platform(),
    )
    backend = acquire_artifact(
        lock,
        tool="uv-build",
        platform=_toolchain_platform(),
        destination=wheelhouse,
        offline=False,
    )
    backend_bytes = backend.read_bytes()
    assert hashlib.sha256(backend_bytes).hexdigest() == backend_locked["sha256"]
    uv_archive = acquire_artifact(
        lock,
        tool="uv",
        platform=_toolchain_platform(),
        destination=wheelhouse,
        offline=False,
    )
    uv_bytes = uv_archive.read_bytes()
    assert hashlib.sha256(uv_bytes).hexdigest() == uv_locked["sha256"]
    expected = _python_policy()

    builds: list[object] = []
    sources: list[Path] = []
    bundles: list[object] = []
    for index in (1, 2):
        source = root / f"source-{index}"
        _clean_archive_source(source, reverse=index == 2, dirty=index == 2)
        sources.append(source)
        bundle = _canonical_bundle(source)
        bundles.append(bundle)
        if index == 2:
            race_canary = source / "src/zagrosi_forge/install/race-canary.py"
            race_canary.write_bytes(DIRTY_BUILD_CANARY)
        builds.append(
            api.build_python_artifacts(
                bundle,
                expected=expected,
                uv_artifact=uv_bytes,
                backend_artifact=backend_bytes,
            )
        )

    rebuilt = api.build_wheel_from_sdist(
        builds[0].sdist_bytes,
        bundle=bundles[0],
        expected=expected,
        uv_artifact=uv_bytes,
        backend_artifact=backend_bytes,
    )
    constraints_path = sources[0] / "build-constraints.txt"
    constraints_bytes = constraints_path.read_bytes()
    return _PythonBuilds(
        first=builds[0],
        second=builds[1],
        rebuilt=rebuilt,
        uv_bytes=uv_bytes,
        uv_sha256=uv_locked["sha256"],
        backend_bytes=backend_bytes,
        backend_sha256=backend_locked["sha256"],
        constraints_bytes=constraints_bytes,
        constraints_sha256=hashlib.sha256(constraints_bytes).hexdigest(),
        source_roots=(sources[0], sources[1]),
        bundles=(bundles[0], bundles[1]),
        wheelhouse=wheelhouse,
        constraints_path=constraints_path,
    )


def _project_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["version"]


def _inspect_python(raw: bytes, *, kind: str) -> object:
    return _api().inspect_python_artifact(
        raw,
        artifact_kind=kind,
        expected=_python_policy(),
    )


def _assert_comparison(left: object, right: object, *, contract: str) -> None:
    comparison = _api().compare_normalized_artifacts(
        left,
        right,
        contract=contract,
    )
    assert comparison.matches
    assert comparison.differences == ()


def test_two_clean_archives_have_equal_normalized_wheel_sdist_manifests(
    python_builds: _PythonBuilds,
) -> None:
    first_wheel = _inspect_python(python_builds.first.wheel_bytes, kind="wheel")
    second_wheel = _inspect_python(python_builds.second.wheel_bytes, kind="wheel")
    first_sdist = _inspect_python(python_builds.first.sdist_bytes, kind="sdist")
    second_sdist = _inspect_python(python_builds.second.sdist_bytes, kind="sdist")

    _assert_comparison(first_wheel, second_wheel, contract="same-kind-v1")
    _assert_comparison(first_sdist, second_sdist, contract="same-kind-v1")
    assert first_wheel.normalized_manifest_digest == (
        second_wheel.normalized_manifest_digest
    )
    assert first_sdist.normalized_manifest_digest == (
        second_sdist.normalized_manifest_digest
    )


def test_clean_and_canary_dirty_archives_build_identical_artifacts(
    python_builds: _PythonBuilds,
) -> None:
    assert python_builds.first.wheel_bytes == python_builds.second.wheel_bytes
    assert python_builds.first.sdist_bytes == python_builds.second.sdist_bytes
    for raw in (
        python_builds.first.wheel_bytes,
        python_builds.first.sdist_bytes,
        python_builds.second.wheel_bytes,
        python_builds.second.sdist_bytes,
    ):
        assert DIRTY_BUILD_CANARY not in raw


def test_wheel_built_from_sdist_matches_declared_normalized_contract(
    python_builds: _PythonBuilds,
) -> None:
    direct = _inspect_python(python_builds.first.wheel_bytes, kind="wheel")
    rebuilt = _inspect_python(python_builds.rebuilt.wheel_bytes, kind="wheel")
    source = _inspect_python(python_builds.first.sdist_bytes, kind="sdist")

    _assert_comparison(direct, rebuilt, contract="same-kind-v1")
    _assert_comparison(source, rebuilt, contract="wheel-from-sdist-v1")


def test_wheel_from_sdist_comparison_rejects_extra_top_level_package(
    python_builds: _PythonBuilds,
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(
        io.BytesIO(python_builds.rebuilt.wheel_bytes), mode="r"
    ) as source:
        with zipfile.ZipFile(output, mode="w") as destination:
            for info in source.infolist():
                destination.writestr(info, source.read(info))
            info = zipfile.ZipInfo("unexpected_package/__init__.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            destination.writestr(info, b"")

    assert (
        _failure_code(lambda: _inspect_python(output.getvalue(), kind="wheel"))
        == "artifact.manifest_mismatch"
    )


def test_artifact_evidence_is_sealed_before_comparison(
    python_builds: _PythonBuilds,
) -> None:
    api = _api()
    source = _inspect_python(python_builds.first.sdist_bytes, kind="sdist")
    wheel = _inspect_python(python_builds.rebuilt.wheel_bytes, kind="wheel")
    with pytest.raises(TypeError):
        replace(wheel, members=wheel.members)  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        replace(python_builds.first, wheel_bytes=python_builds.first.wheel_bytes)
    identity = api.validate_offline_build_inputs(
        uv_artifact=python_builds.uv_bytes,
        backend_artifact=python_builds.backend_bytes,
    )
    with pytest.raises(TypeError):
        replace(identity, backend_sha256=identity.backend_sha256)
    with pytest.raises(TypeError):
        replace(
            python_builds.first.offline_evidence,
            returncode=python_builds.first.offline_evidence.returncode,
        )
    mutated_wheel = _inspect_python(python_builds.rebuilt.wheel_bytes, kind="wheel")
    object.__setattr__(mutated_wheel, "version", "0.2.1")
    with pytest.raises(TypeError):
        api.compare_normalized_artifacts(
            source, mutated_wheel, contract="wheel-from-sdist-v1"
        )
    object.__setattr__(identity, "backend_sha256", "f" * 64)
    assert not api._is_offline_identity(identity)

    policy = _python_policy()
    object.__setattr__(policy, "source_include", ("forged.py",))
    assert (
        _failure_code(
            lambda: api.inspect_python_artifact(
                python_builds.first.wheel_bytes,
                artifact_kind="wheel",
                expected=policy,
            )
        )
        == "bundle.policy_invalid"
    )

    original_result_bytes = python_builds.first.wheel_bytes
    object.__setattr__(python_builds.first, "wheel_bytes", b"forged")
    with pytest.raises(TypeError):
        api.validate_python_build_result(python_builds.first)
    object.__setattr__(python_builds.first, "wheel_bytes", original_result_bytes)
    assert api.validate_python_build_result(python_builds.first) is python_builds.first

    evidence = python_builds.first.offline_evidence
    original_returncode = evidence.returncode
    object.__setattr__(evidence, "returncode", 1)
    assert not api._is_offline_evidence(evidence)
    with pytest.raises(TypeError):
        api.validate_python_build_result(python_builds.first)
    object.__setattr__(evidence, "returncode", original_returncode)
    assert api.validate_python_build_result(python_builds.first) is python_builds.first

    forged = object.__new__(type(wheel))
    with pytest.raises(TypeError):
        api.compare_normalized_artifacts(source, forged, contract="wheel-from-sdist-v1")


def _installed_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _installed_console(environment: Path) -> Path:
    return environment / (
        "Scripts/zagrosi-forge.exe" if os.name == "nt" else "bin/zagrosi-forge"
    )


def test_wheel_and_sdist_install_and_import_in_empty_environments(
    python_builds: _PythonBuilds,
    tmp_path: Path,
) -> None:
    products = (
        ("wheel", python_builds.first.wheel_name, python_builds.first.wheel_bytes),
        ("sdist", python_builds.first.sdist_name, python_builds.first.sdist_bytes),
    )
    for index, (role, name, raw) in enumerate(products, start=1):
        artifact = tmp_path / role / name
        artifact.parent.mkdir()
        artifact.write_bytes(raw)
        environment = tmp_path / f"venv-{role}"
        process_environment = _build_environment(tmp_path, index=index)
        _run(
            "uv",
            "venv",
            "--python",
            sys.executable,
            "--no-project",
            str(environment),
            env=process_environment,
        )
        python = _installed_python(environment)
        install_arguments = [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--offline",
            "--no-index",
            "--no-deps",
        ]
        if role == "sdist":
            install_arguments.extend(
                (
                    "--find-links",
                    str(python_builds.wheelhouse),
                    "--build-constraints",
                    str(python_builds.constraints_path),
                )
            )
        install_arguments.append(str(artifact))
        _run(*install_arguments, env=process_environment)
        source_roots = tuple(str(path) for path in python_builds.source_roots)
        probe = (
            "import sys; from zagrosi_forge.install import VERSION, main; "
            f"assert VERSION == {_project_version()!r}; assert callable(main); "
            f"assert not any(any(root in item for root in {source_roots!r}) "
            "for item in sys.path)"
        )
        _run(
            str(python),
            "-I",
            "-c",
            probe,
            cwd=artifact.parent,
            env=process_environment,
        )
        assert (
            _run(
                str(_installed_console(environment)),
                "--version",
                cwd=artifact.parent,
                env=process_environment,
            ).stdout
            == f"{_project_version()}\n"
        )


def test_python_artifacts_exclude_plugin_only_and_dirty_content(
    python_builds: _PythonBuilds,
) -> None:
    forbidden_parts = frozenset(
        {
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "assets",
            "examples",
            "planning",
            "scripts",
            "skills",
            "tests",
        }
    )
    for raw, kind in (
        (python_builds.first.wheel_bytes, "wheel"),
        (python_builds.first.sdist_bytes, "sdist"),
    ):
        evidence = _inspect_python(raw, kind=kind)
        assert DIRTY_BUILD_CANARY not in raw
        for path in _member_map(evidence):
            assert not forbidden_parts.intersection(PurePosixPath(path).parts), path
            assert not path.endswith((".pyc", ".pyo"))
            assert "secret-canary" not in path


def _wheel_with_extra_empty_directory(raw: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw), mode="r") as source:
        with zipfile.ZipFile(output, mode="w") as destination:
            for info in source.infolist():
                destination.writestr(info, source.read(info))
            extra = zipfile.ZipInfo("unexpected-empty/")
            extra.create_system = 3
            extra.external_attr = (stat.S_IFDIR | 0o755) << 16
            destination.writestr(extra, b"")
    return output.getvalue()


def _sdist_with_extra_empty_directory(raw: bytes) -> bytes:
    copied: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        for item in source.getmembers():
            stream = source.extractfile(item) if item.isfile() else None
            copied.append((item, None if stream is None else stream.read()))
    root = copied[0][0].name.split("/", maxsplit=1)[0]
    extra = tarfile.TarInfo(f"{root}/unexpected-empty")
    extra.type = tarfile.DIRTYPE
    extra.mode = 0o755
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as destination:
        for item, data in copied:
            destination.addfile(item, None if data is None else io.BytesIO(data))
        destination.addfile(extra)
    return output.getvalue()


def _rewrite_wheel_container(
    raw: bytes,
    *,
    drop_first_directory: bool = False,
    first_directory_mode: int | None = None,
    first_directory_data: bytes | None = None,
    compress_empty_directory: bool = False,
    metadata_noise: bool = False,
) -> bytes:
    copied: list[tuple[zipfile.ZipInfo, bytes]] = []
    dropped = False
    changed_mode = False
    with zipfile.ZipFile(io.BytesIO(raw), mode="r") as source:
        for original in source.infolist():
            info = copy(original)
            if info.is_dir() and drop_first_directory and not dropped:
                dropped = True
                continue
            selected_data = source.read(original)
            if info.is_dir() and not changed_mode:
                if first_directory_mode is not None:
                    info.external_attr = (stat.S_IFDIR | first_directory_mode) << 16
                    changed_mode = True
                elif first_directory_data is not None:
                    selected_data = first_directory_data
                    changed_mode = True
                elif compress_empty_directory:
                    info.compress_type = zipfile.ZIP_DEFLATED
                    changed_mode = True
            if metadata_noise:
                info.date_time = (2001, 2, 3, 4, 5, 6)
                info.comment = b"ignored member comment"
                info.extra = b"\x0a\x00\x00\x00"
                info.create_system = 0
                info.compress_type = zipfile.ZIP_STORED
                info.internal_attr = 1
            copied.append((info, selected_data))
    if metadata_noise:
        copied.reverse()
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as destination:
        if metadata_noise:
            destination.comment = b"ignored archive comment"
        for info, data in copied:
            destination.writestr(info, data)
    return output.getvalue()


def _rewrite_sdist_container(
    raw: bytes,
    *,
    drop_first_directory: bool = False,
    first_directory_mode: int | None = None,
    first_directory_data: bytes | None = None,
    root_mode: int | None = None,
    duplicate_root: bool = False,
    replace_path: str | None = None,
    replacement: bytes = b"",
    metadata_noise: bool = False,
) -> bytes:
    copied: list[tuple[tarfile.TarInfo, bytes | None]] = []
    dropped = False
    changed_mode = False
    root_name = ""
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        for original in source.getmembers():
            item = copy(original)
            selected_directory = False
            parts = item.name.rstrip("/").split("/")
            if len(parts) == 1:
                root_name = parts[0]
                if root_mode is not None:
                    item.mode = root_mode
            elif item.isdir() and drop_first_directory and not dropped:
                dropped = True
                continue
            elif item.isdir() and not changed_mode:
                if first_directory_mode is not None:
                    item.mode = first_directory_mode
                    changed_mode = True
                elif first_directory_data is not None:
                    item.size = len(first_directory_data)
                    changed_mode = True
                    selected_directory = True
            stream = source.extractfile(original) if original.isfile() else None
            data = None if stream is None else stream.read()
            if selected_directory and first_directory_data is not None:
                data = first_directory_data
            if replace_path is not None and "/".join(parts[1:]) == replace_path:
                data = replacement
                item.size = len(replacement)
            if metadata_noise:
                item.uid = 137
                item.gid = 211
                item.uname = "ignored-user"
                item.gname = "ignored-group"
                item.mtime = 987654321
                item.pax_headers = {"comment": "ignored pax metadata"}
            copied.append((item, data))
    assert root_name
    if metadata_noise:
        copied.reverse()
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT
    ) as archive:
        for item, data in copied:
            archive.addfile(item, None if data is None else io.BytesIO(data))
        if duplicate_root:
            duplicate = tarfile.TarInfo(root_name)
            duplicate.type = tarfile.DIRTYPE
            duplicate.mode = 0o755
            archive.addfile(duplicate)
    return output.getvalue()


def _sdist_with_nonzero_member_padding(raw: bytes) -> bytes:
    expanded = bytearray(gzip.decompress(raw))
    offset = 0
    while offset + tarfile.BLOCKSIZE <= len(expanded):
        block = bytes(expanded[offset : offset + tarfile.BLOCKSIZE])
        if block == tarfile.NUL * tarfile.BLOCKSIZE:
            break
        item = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
        data_end = offset + tarfile.BLOCKSIZE + item.size
        next_offset = (
            offset
            + tarfile.BLOCKSIZE
            + ((item.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE)
            * tarfile.BLOCKSIZE
        )
        if data_end < next_offset:
            expanded[data_end] = 1
            return gzip.compress(bytes(expanded), compresslevel=9, mtime=0)
        offset = next_offset
    raise AssertionError("sdist has no member padding")


@pytest.mark.parametrize("kind", ("wheel", "sdist"))
def test_python_inspector_rejects_unexpected_empty_directory(
    python_builds: _PythonBuilds,
    kind: str,
) -> None:
    if kind == "wheel":
        raw = _wheel_with_extra_empty_directory(python_builds.first.wheel_bytes)
    else:
        raw = _sdist_with_extra_empty_directory(python_builds.first.sdist_bytes)

    assert _failure_code(lambda: _inspect_python(raw, kind=kind)) == (
        "artifact.manifest_mismatch"
    )


@pytest.mark.parametrize("kind", ("wheel", "sdist"))
@pytest.mark.parametrize("mutation", ("missing", "mode", "payload"))
def test_python_inspector_rejects_directory_inventory_or_mode_drift(
    python_builds: _PythonBuilds,
    kind: str,
    mutation: str,
) -> None:
    if kind == "wheel":
        raw = _rewrite_wheel_container(
            python_builds.first.wheel_bytes,
            drop_first_directory=mutation == "missing",
            first_directory_mode=0o700 if mutation == "mode" else None,
            first_directory_data=b"hidden" if mutation == "payload" else None,
        )
    else:
        raw = _rewrite_sdist_container(
            python_builds.first.sdist_bytes,
            drop_first_directory=mutation == "missing",
            first_directory_mode=0o700 if mutation == "mode" else None,
            first_directory_data=b"hidden" if mutation == "payload" else None,
        )
    expected_code = (
        "artifact.unsafe_member"
        if mutation == "payload"
        else "artifact.manifest_mismatch"
    )
    assert _failure_code(lambda: _inspect_python(raw, kind=kind)) == expected_code


def test_wheel_rejects_compressed_payload_for_empty_directory(
    python_builds: _PythonBuilds,
) -> None:
    changed = _rewrite_wheel_container(
        python_builds.first.wheel_bytes, compress_empty_directory=True
    )
    assert _failure_code(lambda: _inspect_python(changed, kind="wheel")) == (
        "artifact.unsafe_member"
    )


def test_sdist_rejects_nonzero_member_padding(
    python_builds: _PythonBuilds,
) -> None:
    changed = _sdist_with_nonzero_member_padding(python_builds.first.sdist_bytes)
    assert _failure_code(lambda: _inspect_python(changed, kind="sdist")) == (
        "artifact.unsafe_member"
    )


def test_sdist_rejects_duplicate_root_and_noncanonical_root_mode(
    python_builds: _PythonBuilds,
) -> None:
    duplicate = _rewrite_sdist_container(
        python_builds.first.sdist_bytes, duplicate_root=True
    )
    bad_mode = _rewrite_sdist_container(
        python_builds.first.sdist_bytes, root_mode=0o700
    )
    assert _failure_code(lambda: _inspect_python(duplicate, kind="sdist")) == (
        "artifact.metadata_invalid"
    )
    assert _failure_code(lambda: _inspect_python(bad_mode, kind="sdist")) == (
        "artifact.manifest_mismatch"
    )


def test_python_evidence_binds_exact_directory_inventory_and_modes(
    python_builds: _PythonBuilds,
) -> None:
    api = _api()
    for raw, kind in (
        (python_builds.first.wheel_bytes, "wheel"),
        (python_builds.first.sdist_bytes, "sdist"),
    ):
        evidence = _inspect_python(raw, kind=kind)
        expected_paths = api._directory_ancestors(
            tuple(member.path for member in evidence.members)
        )
        assert tuple(directory.path for directory in evidence.directories) == (
            expected_paths
        )
        assert {directory.mode for directory in evidence.directories} == {0o755}
        assert evidence.root_directory_mode == (0o755 if kind == "sdist" else None)


def test_closed_container_metadata_exclusions_are_the_only_normalized_noise(
    python_builds: _PythonBuilds,
) -> None:
    assert _api()._PYTHON_CONTAINER_METADATA_EXCLUSIONS == (
        (
            "wheel-zip",
            (
                "archive-comment",
                "member-comment",
                "member-compressed-size",
                "member-compression-container",
                "member-create-system",
                "member-data-descriptor-signature",
                "member-deflate-representation",
                "member-dos-attribute-bits",
                "member-extra",
                "member-file-permission-bits",
                "member-flag-data-descriptor",
                "member-header-offset",
                "member-internal-attributes",
                "member-name-encoding-flag",
                "member-order",
                "member-timestamp",
                "member-version-fields",
            ),
        ),
        (
            "sdist-tar-gzip",
            (
                "gzip-comment",
                "gzip-deflate-representation",
                "gzip-extra-field",
                "gzip-extra-flags",
                "gzip-filename",
                "gzip-header-crc",
                "gzip-mtime",
                "gzip-os",
                "gzip-text-flag",
                "member-checksum-container",
                "member-file-permission-bits",
                "member-gid",
                "member-gname",
                "member-header-format",
                "member-mtime",
                "member-order",
                "member-pax-headers",
                "member-regular-type-encoding",
                "member-uid",
                "member-uname",
                "tar-end-padding",
            ),
        ),
    )
    policy = _python_policy()
    assert policy.normalization_profile == "python-artifact-v1"
    assert (
        policy.container_metadata_exclusions
        == _api()._PYTHON_CONTAINER_METADATA_EXCLUSIONS
    )
    object.__setattr__(policy, "normalization_profile", "forged-profile")
    assert not _api()._is_python_policy(policy)
    wheel = _inspect_python(python_builds.first.wheel_bytes, kind="wheel")
    noisy_wheel = _inspect_python(
        _rewrite_wheel_container(python_builds.first.wheel_bytes, metadata_noise=True),
        kind="wheel",
    )
    sdist = _inspect_python(python_builds.first.sdist_bytes, kind="sdist")
    noisy_sdist = _inspect_python(
        _rewrite_sdist_container(python_builds.first.sdist_bytes, metadata_noise=True),
        kind="sdist",
    )
    _assert_comparison(wheel, noisy_wheel, contract="same-kind-v1")
    _assert_comparison(sdist, noisy_sdist, contract="same-kind-v1")


def test_python_inspector_rejects_unsupported_container_headers() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr("unsupported.bin", b"unsupported compression header")
    assert _failure_code(lambda: _inspect_python(output.getvalue(), kind="wheel")) == (
        "artifact.unsafe_member"
    )
    assert _failure_code(
        lambda: _inspect_python(_unsafe_sdist("link"), kind="sdist")
    ) == ("artifact.unsafe_member")


@pytest.mark.parametrize("kind", ("wheel", "sdist"))
@pytest.mark.parametrize("position", ("prefix", "magic-prefix", "suffix"))
def test_python_inspector_rejects_bytes_outside_container_framing(
    python_builds: _PythonBuilds,
    kind: str,
    position: str,
) -> None:
    original = (
        python_builds.first.wheel_bytes
        if kind == "wheel"
        else python_builds.first.sdist_bytes
    )
    if position == "prefix":
        changed = b"SHELL-STUB\n" + original
    elif position == "magic-prefix":
        changed = b"PK\x03\x04SFX-STUB" + original
    else:
        changed = original + b"TAIL"
    assert _failure_code(lambda: _inspect_python(changed, kind=kind)) == (
        "artifact.unsafe_member"
    )


def test_sdist_inspector_rejects_concatenated_gzip_members(
    python_builds: _PythonBuilds,
) -> None:
    changed = python_builds.first.sdist_bytes + python_builds.first.sdist_bytes
    assert _failure_code(lambda: _inspect_python(changed, kind="sdist")) == (
        "artifact.unsafe_member"
    )


def test_artifact_versions_match_project_metadata_and_base_manifest(
    python_builds: _PythonBuilds,
    plugin_authority: _PluginAuthority,
) -> None:
    version = _project_version()
    plugin = _inspect_plugin(
        _api().write_controlled_plugin_zip(
            plugin_authority.bundle, plugin_authority.policy
        ),
        authority=plugin_authority,
    )
    wheel = _inspect_python(python_builds.first.wheel_bytes, kind="wheel")
    sdist = _inspect_python(python_builds.first.sdist_bytes, kind="sdist")
    manifest = json.loads(plugin_authority.bundle.manifest_bytes)  # type: ignore[attr-defined]

    assert manifest["base_version"] == version
    assert plugin.version == version
    assert wheel.version == version
    assert sdist.version == version
    assert python_builds.first.wheel_name.startswith("zagrosi_forge-0.2.0-")
    assert python_builds.first.sdist_name == "zagrosi_forge-0.2.0.tar.gz"


def test_offline_build_records_exact_backend_artifact_identity(
    python_builds: _PythonBuilds,
) -> None:
    assert python_builds.first.base_payload_digest == (
        python_builds.bundles[0].manifest.payload_digest
    )
    assert python_builds.second.base_payload_digest == (
        python_builds.bundles[1].manifest.payload_digest
    )
    assert python_builds.rebuilt.base_payload_digest == (
        python_builds.bundles[0].manifest.payload_digest
    )
    for build in (python_builds.first, python_builds.second, python_builds.rebuilt):
        assert build.uv_version == "0.11.23"
        assert build.uv_sha256 == python_builds.uv_sha256
        assert build.backend_name == "uv-build"
        assert build.backend_version == "0.11.28"
        assert build.backend_sha256 == python_builds.backend_sha256
        assert build.constraints_sha256 == python_builds.constraints_sha256
        assert build.artifact_policy_digest == _python_policy().policy_digest
        assert build.offline_evidence.resolver_mode == (
            "offline-no-index-fresh-cache-verified-wheelhouse"
        )
        assert build.offline_evidence.egress == "not_claimed"
        assert build.offline_evidence.returncode == 0
        assert build.offline_evidence.captured_output_bytes <= 1024 * 1024
        assert build.offline_evidence.captured_tail_bytes <= 16 * 1024


def test_build_environment_uses_only_fresh_private_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "secret-canary")
    monkeypatch.setenv("UV_CACHE_DIR", "secret-canary")
    monkeypatch.setenv("PYTHONPATH", "secret-canary")
    executable = tmp_path / "tool" / ("uv.exe" if os.name == "nt" else "uv")
    executable.parent.mkdir()
    executable.write_bytes(b"")

    selected = _api()._build_environment(tmp_path / "environment", executable)

    assert "secret-canary" not in repr(selected)
    assert selected["SOURCE_DATE_EPOCH"] == FIXED_SOURCE_DATE_EPOCH
    assert selected["PYTHONHASHSEED"] == "0"
    assert selected["TZ"] == "UTC"
    assert selected["LC_ALL"] == selected["LANG"] == "C"
    for name in (
        "HOME",
        "TEMP",
        "TMP",
        "TMPDIR",
        "UV_CACHE_DIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "APPDATA",
        "LOCALAPPDATA",
    ):
        path = Path(selected[name])
        assert path.is_dir()
        assert path.is_relative_to(tmp_path / "environment")


def test_build_fails_when_constraint_or_backend_digest_drifts(
    python_builds: _PythonBuilds,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _api()
    identity = api.validate_offline_build_inputs(
        uv_artifact=python_builds.uv_bytes,
        backend_artifact=python_builds.backend_bytes,
    )
    assert identity.uv_sha256 == python_builds.uv_sha256
    assert identity.backend_sha256 == python_builds.backend_sha256
    assert api._trusted_constraints_bytes() == python_builds.constraints_bytes

    invoked = False

    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        nonlocal invoked
        invoked = True
        raise AssertionError("uv was invoked")

    monkeypatch.setattr(api, "_bounded_process", forbidden_process)
    self_consistent_backend = _raw_zip(
        (
            (
                "uv_build-0.11.28.dist-info/METADATA",
                b"Metadata-Version: 2.3\nName: uv-build\nVersion: 0.11.28\n",
                stat.S_IFREG | 0o644,
            ),
        )
    )
    for uv_artifact, backend_artifact in (
        (python_builds.uv_bytes + b"changed", python_builds.backend_bytes),
        (python_builds.uv_bytes, python_builds.backend_bytes + b"changed"),
        (python_builds.uv_bytes, self_consistent_backend),
    ):
        assert (
            _failure_code(
                lambda uv_artifact=uv_artifact, backend_artifact=backend_artifact: (
                    api.build_python_artifacts(
                        python_builds.bundles[0],
                        expected=_python_policy(),
                        uv_artifact=uv_artifact,
                        backend_artifact=backend_artifact,
                    )
                )
            )
            == "artifact.backend_identity_mismatch"
        )
    assert not invoked


def test_malicious_bundle_build_profile_is_rejected_before_spawn(
    python_builds: _PythonBuilds,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "malicious-candidate"
    shutil.copytree(python_builds.source_roots[0], source)
    marker = tmp_path / "backend-executed"
    pyproject = (source / "pyproject.toml").read_text(encoding="utf-8")
    pyproject = pyproject.replace(
        'build-backend = "uv_build"',
        'build-backend = "zagrosi_forge"\nbackend-path = ["src"]',
        1,
    )
    (source / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (source / "src/zagrosi_forge/__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    bundle = _canonical_bundle(source)
    invoked = False

    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        nonlocal invoked
        invoked = True
        raise AssertionError("uv was invoked")

    monkeypatch.setattr(_api(), "_bounded_process", forbidden_process)
    forged_policy = _python_policy()
    object.__setattr__(forged_policy, "source_include", ("forged.py",))
    assert (
        _failure_code(
            lambda: _api().build_python_artifacts(
                python_builds.bundles[0],
                expected=forged_policy,
                uv_artifact=python_builds.uv_bytes,
                backend_artifact=python_builds.backend_bytes,
            )
        )
        == "bundle.policy_invalid"
    )
    assert (
        _failure_code(
            lambda: _api().build_python_artifacts(
                bundle,
                expected=_python_policy(),
                uv_artifact=python_builds.uv_bytes,
                backend_artifact=python_builds.backend_bytes,
            )
        )
        == "artifact.metadata_invalid"
    )
    assert not invoked
    assert not marker.exists()


def test_sdist_pyproject_and_payload_drift_are_rejected_before_spawn(
    python_builds: _PythonBuilds,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_pyproject = python_builds.bundles[0].entry_bytes["pyproject.toml"]
    malicious_pyproject = original_pyproject.replace(
        b'build-backend = "uv_build"',
        b'build-backend = "malicious_backend"',
        1,
    )
    profile_drift = _rewrite_sdist_container(
        python_builds.first.sdist_bytes,
        replace_path="pyproject.toml",
        replacement=malicious_pyproject,
    )
    payload_drift = _rewrite_sdist_container(
        python_builds.first.sdist_bytes,
        replace_path="src/zagrosi_forge/__init__.py",
        replacement=b"# canonical payload drift\n",
    )
    invoked = False

    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        nonlocal invoked
        invoked = True
        raise AssertionError("uv was invoked")

    monkeypatch.setattr(_api(), "_bounded_process", forbidden_process)
    common = {
        "bundle": python_builds.bundles[0],
        "expected": _python_policy(),
        "uv_artifact": python_builds.uv_bytes,
        "backend_artifact": python_builds.backend_bytes,
    }
    assert (
        _failure_code(lambda: _api().build_wheel_from_sdist(profile_drift, **common))
        == "artifact.metadata_invalid"
    )
    assert (
        _failure_code(lambda: _api().build_wheel_from_sdist(payload_drift, **common))
        == "artifact.reproducibility_mismatch"
    )
    assert not invoked


def _unsafe_sdist(case: str) -> bytes:
    root = "zagrosi_forge-0.2.0"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        if case == "traversal":
            item = tarfile.TarInfo("../outside")
            item.size = 1
            archive.addfile(item, io.BytesIO(b"x"))
        elif case == "link":
            item = tarfile.TarInfo(f"{root}/linked")
            item.type = tarfile.SYMTYPE
            item.linkname = "../outside"
            archive.addfile(item)
        elif case == "duplicate":
            for raw in (b"a", b"b"):
                item = tarfile.TarInfo(f"{root}/duplicate.txt")
                item.size = len(raw)
                archive.addfile(item, io.BytesIO(raw))
        elif case == "directory-payload":
            item = tarfile.TarInfo(root)
            item.type = tarfile.DIRTYPE
            item.mode = 0o755
            item.size = 1
            archive.addfile(item, io.BytesIO(b"x"))
        elif case == "limit":
            raw = b"x" * (16 * 1024 * 1024 + 1)
            item = tarfile.TarInfo(f"{root}/oversized.bin")
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
        else:
            raise AssertionError(case)
    return output.getvalue()


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("traversal", "artifact.unsafe_member"),
        ("link", "artifact.unsafe_member"),
        ("duplicate", "artifact.unsafe_member"),
        ("directory-payload", "artifact.unsafe_member"),
        ("limit", "bundle.limit_exceeded"),
    ),
)
def test_wheel_from_sdist_rejects_unsafe_members_before_uv(
    case: str,
    expected_code: str,
    python_builds: _PythonBuilds,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def forbidden_run(*_args: object, **_kwargs: object) -> object:
        nonlocal invoked
        invoked = True
        raise AssertionError("uv was invoked")

    monkeypatch.setattr(_api(), "_bounded_process", forbidden_run)
    assert (
        _failure_code(
            lambda: _api().build_wheel_from_sdist(
                _unsafe_sdist(case),
                bundle=python_builds.bundles[0],
                expected=_python_policy(),
                uv_artifact=python_builds.uv_bytes,
                backend_artifact=python_builds.backend_bytes,
            )
        )
        == expected_code
    )
    assert not invoked


def test_build_records_offline_fresh_resolver_configuration(
    python_builds: _PythonBuilds,
) -> None:
    required_flags = {
        "--build-constraints",
        "--find-links",
        "--no-config",
        "--no-index",
        "--no-managed-python",
        "--no-sources",
        "--offline",
        "--require-hashes",
    }
    for build in (python_builds.first, python_builds.second, python_builds.rebuilt):
        assert required_flags <= set(build.command)
        assert required_flags <= set(build.offline_evidence.command_flags)
        assert dict(build.offline_evidence.environment) == {
            "PIP_NO_INDEX": "1",
            "UV_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
        assert build.offline_evidence.resolver_mode == (
            "offline-no-index-fresh-cache-verified-wheelhouse"
        )
        assert build.offline_evidence.egress == "not_claimed"


def test_bounded_process_timeout_kills_descendant_tree(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-survived"
    child = (
        "import time; from pathlib import Path; "
        "time.sleep(2); "
        f"Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "time.sleep(30)"
    )

    assert (
        _failure_code(
            lambda: _api()._bounded_process(
                (sys.executable, "-c", parent),
                cwd=tmp_path,
                environment=os.environ,
                umask=0o077,
                timeout=1,
                channel_limit=1024,
                tail_limit=128,
                failure_code="artifact.build_failed",
                failure_message="bounded process failed safely",
            )
        )
        == "artifact.build_failed"
    )
    time.sleep(2.5)
    assert not marker.exists()


def test_build_evidence_never_retains_disposable_absolute_paths(
    python_builds: _PythonBuilds,
) -> None:
    for build in (python_builds.first, python_builds.second, python_builds.rebuilt):
        rendered = repr((build.command, build.offline_evidence))
        assert all(str(root) not in rendered for root in python_builds.source_roots)
        assert "secret-canary" not in rendered
        assert "<verified-wheelhouse>" in build.command
        assert "<verified-constraints>" in build.command
        assert "<private-output>" in build.command


def _source_package_data() -> set[str]:
    selected = set()
    for path in (ROOT / "src/zagrosi_forge/install").glob("*.json"):
        selected.add(path.relative_to(ROOT / "src").as_posix())
    for path in (ROOT / "src/zagrosi_forge/install/schemas").glob("*.json"):
        selected.add(path.relative_to(ROOT / "src").as_posix())
    for name in ("tomlkit-LICENSE", "vendor-receipt.json"):
        selected.add(f"zagrosi_forge/_vendor/{name}")
    return selected


def test_component_vendor_schema_license_and_notice_members_are_exact(
    python_builds: _PythonBuilds,
) -> None:
    wheel = _member_map(_inspect_python(python_builds.first.wheel_bytes, kind="wheel"))
    sdist = _member_map(_inspect_python(python_builds.first.sdist_bytes, kind="sdist"))
    expected_package_data = _source_package_data()
    actual_package_data = {
        path
        for path in wheel
        if (path.startswith("zagrosi_forge/install/") and path.endswith(".json"))
        or path
        in {
            "zagrosi_forge/_vendor/tomlkit-LICENSE",
            "zagrosi_forge/_vendor/vendor-receipt.json",
        }
    }

    assert actual_package_data == expected_package_data
    assert {
        path
        for path in sdist
        if path in {"LICENSE", "NOTICE.md", "component-inventory.json"}
    } == {"LICENSE", "NOTICE.md", "component-inventory.json"}
    for path in ("LICENSE", "NOTICE.md", "component-inventory.json"):
        expected = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert sdist[path].sha256 == expected  # type: ignore[attr-defined]


def test_direct_script_and_trusted_console_entry_point_use_expected_core(
    python_builds: _PythonBuilds,
    plugin_authority: _PluginAuthority,
) -> None:
    plugin = _member_map(
        _inspect_plugin(
            _api().write_controlled_plugin_zip(
                plugin_authority.bundle, plugin_authority.policy
            ),
            authority=plugin_authority,
        )
    )
    assert set(plugin_authority.policy.executable_files) <= plugin.keys()  # type: ignore[attr-defined]
    assert "src/zagrosi_forge/install/__init__.py" in plugin

    with zipfile.ZipFile(io.BytesIO(python_builds.first.wheel_bytes)) as archive:
        entry_points = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/entry_points.txt")
        ]
        assert len(entry_points) == 1
        rendered = archive.read(entry_points[0]).decode("utf-8")
    assert rendered == (
        "[console_scripts]\nzagrosi-forge = zagrosi_forge.install:main\n\n"
    )
