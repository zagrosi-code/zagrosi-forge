"""Superseded receipt bytes stay inspectable without enlarging current evidence."""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from test_compact_plan import SECTION
from test_verification_receipts import workspace, invoke, verify_args, record_args


@pytest.fixture
def captured(workspace, monkeypatch):
    calls = []

    def capture(command, target, timeout):
        calls.append(command)
        failed = command[-1] == "fail"
        return {"source": "captured", "command": command, "exit_code": 3 if failed else 0,
                "outcome": "failed" if failed else "passed", "seconds": 0.25,
                "stdout_tail": "counter-input rejected" if failed else "counter-input passed",
                "stderr_tail": "", "stdout_truncated": False, "stderr_truncated": False}

    monkeypatch.setattr(workspace[0].verification, "_capture", capture)
    return calls


def verify(workspace, capsys, *selectors, label="pass"):
    return invoke(workspace, capsys, *verify_args(workspace, *selectors, "--", "history-check", label))


def archive(path, raw):
    return path.parent / "history" / path.stem / (hashlib.sha256(raw).hexdigest() + ".json")


def test_history_failed_receipt_survives_later_pass_without_payload_growth(workspace, captured, capsys):
    forge, planning, target = workspace
    assert verify(workspace, capsys, "--section", SECTION, "--integration", label="fail")[0] == 1
    paths = [forge.verification.receipt_path(planning, SECTION), forge.verification.receipt_path(planning)]
    previous = {path: path.read_bytes() for path in paths}
    code, result = verify(workspace, capsys, "--section", SECTION, "--integration")
    assert code == 0 and len(captured) == 2
    assert set(result) == {"success", "receipt_path", "error", "changed_components", "integration_receipt_path",
                           "source", "outcome", "seconds", "exit_code", "stdout_tail", "stderr_tail",
                           "stdout_truncated", "stderr_truncated"}
    for path, raw in previous.items():
        assert archive(path, raw).read_bytes() == raw
        current, old = json.loads(path.read_bytes()), json.loads(raw)
        assert current.keys() == old.keys() and current["snapshot"] == old["snapshot"]
        assert old["outcome"] == "failed" and old["command"][-1] == "fail"
        assert old["stdout_tail"] == "counter-input rejected" and old["seconds"] == 0.25
    assert forge.verification.integration_report(planning, target)["success"]
    code, recorded = invoke(workspace, capsys, *record_args(
        workspace, "--verification-receipt", str(paths[0])))
    assert code == 0 and recorded["record"]["verification_result"] == json.loads(paths[0].read_bytes())


@pytest.mark.parametrize("section_raw", [b'{"outcome":"failed","unknown":"keep"}\r\n', b""], ids=["json", "empty"])
def test_history_paired_scopes_keep_divergent_and_malformed_bytes(workspace, captured, capsys, section_raw):
    forge, planning, _ = workspace
    paths = [forge.verification.receipt_path(planning, SECTION), forge.verification.receipt_path(planning)]
    paths[0].parent.mkdir(parents=True)
    prior = [section_raw, b'\xffmalformed\x00']
    for path, raw in zip(paths, prior):
        path.write_bytes(raw)
    assert verify(workspace, capsys, "--section", SECTION, "--integration")[0] == 0
    assert len(captured) == 1
    for path, raw in zip(paths, prior):
        assert archive(path, raw).read_bytes() == raw
        assert json.loads(path.read_bytes())["outcome"] == "passed"


def test_history_interrupted_pending_is_preserved_without_invented_outcome(workspace, captured, capsys, monkeypatch):
    forge, planning, _ = workspace
    capture = forge.verification._capture

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(forge.verification, "_capture", interrupt)
    with pytest.raises(KeyboardInterrupt):
        verify(workspace, capsys)
    path = forge.verification.receipt_path(planning)
    pending = path.read_bytes()
    monkeypatch.setattr(forge.verification, "_capture", capture)
    assert verify(workspace, capsys)[0] == 0
    assert archive(path, pending).read_bytes() == pending
    saved = json.loads(pending)
    assert saved["outcome"] == "pending" and "seconds" not in saved


@pytest.mark.parametrize("blocker", ["corrupt", "publication"])
def test_history_paired_archive_failure_precedes_current_replacement(workspace, captured, capsys, monkeypatch, blocker):
    forge, planning, _ = workspace
    paths = [forge.verification.receipt_path(planning, SECTION), forge.verification.receipt_path(planning)]
    paths[0].parent.mkdir(parents=True)
    previous = {path: ("retained " + path.name).encode() for path in paths}
    for path, raw in previous.items():
        path.write_bytes(raw)
    blocked = archive(paths[1], previous[paths[1]])
    if blocker == "corrupt":
        blocked.parent.mkdir(parents=True)
        blocked.write_bytes(b"do not overwrite this conflict")
    else:
        replace = forge.storage.os.replace

        def refuse(source, destination):
            if Path(destination) == blocked:
                raise OSError("archive publication refused")
            return replace(source, destination)

        monkeypatch.setattr(forge.storage.os, "replace", refuse)
    code, result = verify(workspace, capsys, "--section", SECTION, "--integration")
    assert code == 1 and not result["success"] and result["error"]
    assert captured == [] and {path: path.read_bytes() for path in paths} == previous
    if blocker == "corrupt":
        assert blocked.read_bytes() == b"do not overwrite this conflict"
    else:
        assert not blocked.exists()
        assert not list(blocked.parent.glob("." + blocked.name + ".*"))


def test_history_identical_archive_is_reused_without_rewriting(workspace, captured, capsys, monkeypatch):
    forge, planning, _ = workspace
    path = forge.verification.receipt_path(planning)
    path.parent.mkdir(parents=True)
    raw = b'{"outcome":"pending"}\r\n'
    path.write_bytes(raw)
    saved = archive(path, raw)
    saved.parent.mkdir(parents=True)
    saved.write_bytes(raw)
    replace = forge.storage.os.replace

    def protect(source, destination):
        assert Path(destination) != saved, "Existing identical archive was rewritten"
        return replace(source, destination)

    monkeypatch.setattr(forge.storage.os, "replace", protect)
    assert verify(workspace, capsys)[0] == 0
    assert saved.read_bytes() == raw and len(captured) == 1


@pytest.mark.parametrize("relative,directory", [
    (".", True), (".mutable-state.lock", False), ("verification/integration.json", False),
    ("verification/history", True), ("verification/history/integration/{digest}.json", False),
])
def test_history_managed_links_refuse_before_opening_external_files(workspace, captured, capsys, tmp_path, monkeypatch, relative, directory):
    _, planning, _ = workspace
    implementation = planning / "implementation"
    if "{digest}" in relative:
        receipt = implementation / "verification/integration.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b"retained receipt")
        relative = relative.format(digest=hashlib.sha256(receipt.read_bytes()).hexdigest())
    link, external = implementation / relative, tmp_path / "external"
    link.parent.mkdir(parents=True, exist_ok=True)
    if directory:
        external.mkdir()
        sentinel = external / "keep.txt"
    else:
        sentinel = external
    sentinel.write_bytes(b"user bytes")
    try:
        link.symlink_to(external, target_is_directory=directory)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM} or getattr(exc, "winerror", None) == 1314:
            pytest.skip("Symlinks are unavailable")
        raise
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(external), "Followed managed link into user files"
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded)
        code, result = verify(workspace, capsys)
    assert code == 1 and not result["success"] and captured == []
    assert link.is_symlink() and sentinel.read_bytes() == b"user bytes"
    if directory:
        assert list(external.iterdir()) == [sentinel]


def test_history_dangling_receipt_link_is_not_treated_as_missing(workspace, captured, capsys, tmp_path):
    forge, planning, _ = workspace
    path, missing = forge.verification.receipt_path(planning), tmp_path / "missing-user-file"
    path.parent.mkdir(parents=True)
    try:
        path.symlink_to(missing)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM} or getattr(exc, "winerror", None) == 1314:
            pytest.skip("Symlinks are unavailable")
        raise
    assert verify(workspace, capsys)[0] == 1
    assert captured == [] and path.is_symlink() and not missing.exists()


def test_history_hardlinked_lock_cannot_append_to_outside_alias(workspace, captured, capsys, tmp_path):
    _, planning, _ = workspace
    lock, external = planning / "implementation/.mutable-state.lock", tmp_path / "external-empty-file"
    lock.parent.mkdir()
    external.write_bytes(b"")
    try:
        os.link(external, lock)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM} or getattr(exc, "winerror", None) == 1314:
            pytest.skip("Hardlinks are unavailable")
        raise
    assert lock.stat().st_nlink == 2
    code, result = verify(workspace, capsys)
    assert code == 1 and not result["success"] and captured == []
    assert external.read_bytes() == b"" and lock.read_bytes() == b""
    assert lock.stat().st_ino == external.stat().st_ino and lock.stat().st_nlink == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_history_windows_junction_refuses_without_external_mutation(workspace, captured, capsys, tmp_path):
    _, planning, _ = workspace
    link, external = planning / "implementation", tmp_path / "external"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_bytes(b"user bytes")
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(external)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        assert verify(workspace, capsys)[0] == 1
        assert captured == [] and list(external.iterdir()) == [sentinel]
        assert sentinel.read_bytes() == b"user bytes"
    finally:
        link.rmdir()


def test_storage_preservation_text_bytes_and_permissions(workspace, tmp_path):
    forge = workspace[0]
    path = tmp_path / "state.json"
    path.write_bytes(b"previous")
    if os.name != "nt":
        path.chmod(0o640)
    payload = {"z": "Unicode \u2028", "a": [1, 2]}
    forge.storage.write_json(path, payload)
    expected = (json.dumps(payload, indent=2, sort_keys=True) + "\n").replace("\n", os.linesep).encode("utf-8")
    assert path.read_bytes() == expected
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_path_preservation_invalid_section_does_not_probe_destination(workspace, captured, capsys, tmp_path, monkeypatch):
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"user bytes")

    def guarded(method):
        def check(path, *args, **kwargs):
            assert path != outside, "Probed receipt derived from an invalid section"
            return method(path, *args, **kwargs)
        return check

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", guarded(Path.stat))
        patch.setattr(Path, "lstat", guarded(Path.lstat))
        code, result = verify(workspace, capsys, "--section", str(outside.with_suffix("")))
    assert code == 1 and result["error"] == "Verification requires a complete section index."
    assert captured == [] and outside.read_bytes() == b"user bytes"


def test_storage_preservation_mkdir_precedes_serialization_and_temp_creation(workspace, tmp_path, monkeypatch):
    forge = workspace[0]
    path = tmp_path / "created-first" / "state.json"
    monkeypatch.setattr(forge.storage.tempfile, "mkstemp", lambda **kwargs: pytest.fail("Temporary file before serialization"))
    with pytest.raises(TypeError):
        forge.storage.write_json(path, {"invalid": object()})
    assert path.parent.is_dir() and list(path.parent.iterdir()) == []
    blocked = tmp_path / "not-a-directory"
    blocked.write_bytes(b"user bytes")
    with pytest.raises(OSError):
        forge.storage.write_json(blocked / "state.json", {"invalid": object()})
    assert blocked.read_bytes() == b"user bytes"
