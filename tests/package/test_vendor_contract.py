from __future__ import annotations

from collections.abc import Callable
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
from typing import BinaryIO

import pytest


ROOT = Path(__file__).parents[2]
VENDOR_ROOT = ROOT / "src/zagrosi_forge/_vendor"
_VENDORED_RUNTIME = (
    "tomlkit/__init__.py",
    "tomlkit/_compat.py",
    "tomlkit/_types.py",
    "tomlkit/_utils.py",
    "tomlkit/api.py",
    "tomlkit/container.py",
    "tomlkit/exceptions.py",
    "tomlkit/items.py",
    "tomlkit/parser.py",
    "tomlkit/py.typed",
    "tomlkit/source.py",
    "tomlkit/toml_char.py",
    "tomlkit/toml_document.py",
    "tomlkit/toml_file.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _selected_sdist_members() -> dict[str, bytes]:
    return {
        "LICENSE": b"MIT\n",
        **{
            name: b"" if name.endswith("py.typed") else b"from tomlkit import loads\n"
            for name in _VENDORED_RUNTIME
        },
    }


def _member_manifest(members: dict[str, bytes]) -> tuple[int, str]:
    rows = [
        f"tomlkit-0.15.0/{name}\0file\0{len(data)}\n" for name, data in members.items()
    ]
    return len(rows), hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()


def _write_selected_sdist(path: Path, members: dict[str, bytes]) -> bytes:
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as output:
        with tarfile.open(fileobj=output, mode="w:") as archive:
            for name, data in members.items():
                info = tarfile.TarInfo(f"tomlkit-0.15.0/{name}")
                info.mode = 0o644
                info.mtime = 0
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    archive_bytes = compressed.getvalue()
    path.write_bytes(archive_bytes)
    return archive_bytes


class _ReadObserver:
    def __init__(
        self,
        stream: BinaryIO,
        *,
        read_sizes: list[int],
        after_close: Callable[[], None] | None = None,
    ) -> None:
        self._stream = stream
        self._read_sizes = read_sizes
        self._after_close = after_close

    def __enter__(self) -> _ReadObserver:
        self._stream.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        result = self._stream.__exit__(*args)
        if self._after_close is not None:
            callback = self._after_close
            self._after_close = None
            callback()
        return result

    def read(self, size: int = -1) -> bytes:
        value = self._stream.read(size)
        self._read_sizes.append(size)
        return value


def test_vendor_receipt_matches_exact_selected_tree() -> None:
    from zagrosi_forge.install.vendor import load_vendor_receipt

    receipt = load_vendor_receipt()
    selected = receipt["selected_files"]
    assert isinstance(selected, tuple)
    expected = {item["path"]: (item["sha256"], item["size"]) for item in selected}
    actual = {
        path.relative_to(VENDOR_ROOT).as_posix(): (_sha256(path), path.stat().st_size)
        for path in sorted((VENDOR_ROOT / "tomlkit").rglob("*"))
        if path.is_file()
        and path.name != "__pycache__"
        and "__pycache__" not in path.parts
    }
    assert actual == expected
    tree_lines = "".join(
        f"{name}\0{digest}\0{size}\n" for name, (digest, size) in sorted(actual.items())
    ).encode()
    assert hashlib.sha256(tree_lines).hexdigest() == receipt["selected_tree_digest"]


def test_vendor_source_hash_license_and_notice_match() -> None:
    from zagrosi_forge.install.vendor import load_vendor_receipt

    receipt = load_vendor_receipt()
    upstream = receipt["upstream"]
    assert (
        upstream["artifact_sha256"]
        == "7d1a9ecba3086638211b13814ea79c90dd54dd11993564376f3aa92271f5c7a3"
    )
    assert (
        upstream["artifact_url"]
        == "https://files.pythonhosted.org/packages/51/db/03eaf4331631ef6b27d6e3c9b68c54dc6f0d63d87201fed600cc409307fd/tomlkit-0.15.0.tar.gz"
    )
    assert upstream["name"] == "tomlkit"
    assert upstream["version"] == "0.15.0"
    assert upstream["verified_tag_commit"] == "8694e4d3323df68eb325bf3d5ab7caa66f8c206a"
    license_path = VENDOR_ROOT / "tomlkit-LICENSE"
    assert _sha256(license_path) == receipt["license"]["sha256"]
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "TOML Kit 0.15.0" in notice
    assert upstream["artifact_sha256"] in notice
    assert receipt["license"]["sha256"] in notice


def test_vendor_imports_on_python_matrix() -> None:
    from zagrosi_forge._vendor import tomlkit

    assert (3, 11) <= sys.version_info[:2] < (3, 15)
    source = "# keep\n[managed]\nenabled = true\n"
    document = tomlkit.parse(source)
    document["managed"]["enabled"] = False
    rendered = tomlkit.dumps(document)
    assert rendered.startswith("# keep\n")
    assert "enabled = false" in rendered

    if os.environ.get("ZAGROSI_REQUIRE_LOCAL_PYTHON_MATRIX") == "1":
        for version in ("3.11", "3.12", "3.13", "3.14"):
            executable = shutil.which(f"python{version}")
            if executable is None:
                located = subprocess.run(
                    ["uv", "python", "find", version],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"},
                )
                executable = located.stdout.strip()
            assert executable and Path(executable).is_file(), version
            subprocess.run(
                [
                    executable,
                    "-I",
                    "-c",
                    (
                        "import sys; "
                        f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
                        "from zagrosi_forge._vendor import tomlkit; "
                        "assert tomlkit.loads('x = 1')['x'] == 1"
                    ),
                ],
                check=True,
            )


def test_vendor_component_is_present_in_advisory_inventory() -> None:
    from zagrosi_forge.install.vendor import load_vendor_receipt

    inventory = json.loads(
        (ROOT / "component-inventory.json").read_text(encoding="utf-8")
    )
    receipt = load_vendor_receipt()
    component = next(
        item for item in inventory["components"] if item["name"] == "tomlkit"
    )
    assert component["kind"] == "vendored-python"
    assert component["version"] == "0.15.0"
    assert component["tree_digest"] == receipt["selected_tree_digest"]
    assert component["license"] == "MIT"
    assert component["advisory_scope"] == "runtime"


def test_component_inventory_covers_exact_locked_graph() -> None:
    inventory_path = ROOT / "component-inventory.json"
    lock_path = ROOT / "uv.lock"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))

    locked_packages = {
        (package["name"], package["version"]): package for package in lock["package"]
    }
    locked_components = {
        (component["name"], component["version"]): component
        for component in inventory["components"]
        if component["kind"] in {"locked-python", "project"}
    }
    assert locked_components.keys() == locked_packages.keys()
    assert inventory["lock_digest"] == _sha256(lock_path)
    assert inventory["component_count"] == len(inventory["components"])

    for identity, package in locked_packages.items():
        component = locked_components[identity]
        assert component["license"]
        assert component["advisory_scope"]
        if component["kind"] == "project":
            assert component["source_authority"] == "pyproject.toml"
            continue
        expected_digests = sorted(
            {
                artifact["hash"].removeprefix("sha256:")
                for artifact in [package.get("sdist", {}), *package.get("wheels", [])]
                if artifact.get("hash")
            }
        )
        assert component["artifact_digests"] == expected_digests
        assert component["source_authority"].startswith("uv.lock;")


def test_component_inventory_generator_is_reproducible(tmp_path: Path) -> None:
    generated = tmp_path / "component-inventory.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/generate_component_inventory.py"),
            "--lock",
            str(ROOT / "uv.lock"),
            "--vendor-receipt",
            str(VENDOR_ROOT / "vendor-receipt.json"),
            "--output",
            str(generated),
        ],
        check=True,
    )
    assert generated.read_bytes() == (ROOT / "component-inventory.json").read_bytes()


def test_vendor_manifest_accepts_controlled_selected_tree_and_hashes_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import vendor_tomlkit

    members = _selected_sdist_members()
    artifact = tmp_path / "tomlkit-0.15.0.tar.gz"
    archive_bytes = _write_selected_sdist(artifact, members)
    count, manifest_digest = _member_manifest(members)
    monkeypatch.setattr(vendor_tomlkit, "AUDITED_MEMBER_COUNT", count)
    monkeypatch.setattr(
        vendor_tomlkit, "AUDITED_MEMBER_MANIFEST_SHA256", manifest_digest
    )
    real_sha256 = vendor_tomlkit._sha256
    artifact_hashes = 0

    def observed_sha256(value: bytes) -> str:
        nonlocal artifact_hashes
        if value == archive_bytes:
            artifact_hashes += 1
        return real_sha256(value)

    monkeypatch.setattr(vendor_tomlkit, "_sha256", observed_sha256)
    selected, license_bytes = vendor_tomlkit._read_archive(
        artifact, hashlib.sha256(archive_bytes).hexdigest()
    )
    assert selected == {
        name: data for name, data in members.items() if name.startswith("tomlkit/")
    }
    assert license_bytes == members["LICENSE"]
    assert artifact_hashes == 1


def test_vendor_manifest_rejects_one_changed_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import vendor_tomlkit

    baseline = _selected_sdist_members()
    count, manifest_digest = _member_manifest(baseline)
    monkeypatch.setattr(vendor_tomlkit, "AUDITED_MEMBER_COUNT", count)
    monkeypatch.setattr(
        vendor_tomlkit, "AUDITED_MEMBER_MANIFEST_SHA256", manifest_digest
    )
    changed = dict(baseline)
    changed["tomlkit/container.py"] += b"x"
    artifact = tmp_path / "tomlkit-0.15.0.tar.gz"
    archive_bytes = _write_selected_sdist(artifact, changed)
    with pytest.raises(
        vendor_tomlkit.VendorError,
        match="^source artifact member manifest mismatch$",
    ):
        vendor_tomlkit._read_archive(
            artifact, hashlib.sha256(archive_bytes).hexdigest()
        )


def test_vendor_source_read_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import vendor_tomlkit

    artifact = tmp_path / "oversized.tar.gz"
    artifact.write_bytes(b"123456789")
    monkeypatch.setattr(vendor_tomlkit, "MAX_ARCHIVE_BYTES", 8)
    real_open = Path.open
    read_sizes: list[int] = []

    def observed_open(self: Path, *args: object, **kwargs: object) -> BinaryIO:
        stream = real_open(self, *args, **kwargs)
        if self == artifact:
            return _ReadObserver(stream, read_sizes=read_sizes)  # type: ignore[arg-type,return-value]
        return stream  # type: ignore[return-value]

    monkeypatch.setattr(Path, "open", observed_open)
    with pytest.raises(
        vendor_tomlkit.VendorError,
        match="^source artifact exceeds the compressed limit$",
    ):
        vendor_tomlkit._read_archive(artifact, "0" * 64)
    assert read_sizes == [9]


def test_vendor_parses_verified_bytes_after_path_swap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import vendor_tomlkit

    members = _selected_sdist_members()
    artifact = tmp_path / "tomlkit-0.15.0.tar.gz"
    archive_bytes = _write_selected_sdist(artifact, members)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"not the verified archive")
    count, manifest_digest = _member_manifest(members)
    monkeypatch.setattr(vendor_tomlkit, "AUDITED_MEMBER_COUNT", count)
    monkeypatch.setattr(
        vendor_tomlkit, "AUDITED_MEMBER_MANIFEST_SHA256", manifest_digest
    )
    real_open = Path.open
    read_sizes: list[int] = []

    def swap_path() -> None:
        os.replace(replacement, artifact)

    def swapping_open(self: Path, *args: object, **kwargs: object) -> BinaryIO:
        stream = real_open(self, *args, **kwargs)
        if self == artifact:
            return _ReadObserver(  # type: ignore[arg-type,return-value]
                stream, read_sizes=read_sizes, after_close=swap_path
            )
        return stream  # type: ignore[return-value]

    monkeypatch.setattr(Path, "open", swapping_open)
    selected, license_bytes = vendor_tomlkit._read_archive(
        artifact, hashlib.sha256(archive_bytes).hexdigest()
    )
    assert set(selected) == set(_VENDORED_RUNTIME)
    assert license_bytes == members["LICENSE"]
    assert read_sizes == [vendor_tomlkit.MAX_ARCHIVE_BYTES + 1]
    with real_open(artifact, "rb") as stream:
        assert stream.read() == b"not the verified archive"


def test_candidate_vendor_receipt_cannot_override_trusted_expectation(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.vendor import load_vendor_receipt

    candidate = tmp_path / "vendor-receipt.json"
    candidate.write_text('{"upstream":{"version":"999.0.0"}}', encoding="utf-8")
    with pytest.raises(ForgeError) as caught:
        load_vendor_receipt(candidate_receipt=candidate)
    assert caught.value.code == "vendor.candidate_authority_rejected"
    assert caught.value.exit_category == 10
