"""Verification binds directory-link entries without opening or traversing them."""

from pathlib import Path
import subprocess

import pytest

from test_verification_receipts import workspace, invoke, verify_args


def symlink(path, destination, *, directory=True):
    try:
        path.symlink_to(destination, target_is_directory=directory)
    except OSError:
        pytest.skip("Symlinks are unavailable")


def inventory(target, git):
    if git:
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=target, check=True, capture_output=True)


def verify(workspace, capsys):
    code, payload = invoke(workspace, capsys, *verify_args(
        workspace, "--source", "attestation", "--outcome", "passed", "--evidence", "Integration checks passed"))
    assert code == 0, payload
    return payload


@pytest.mark.parametrize("git", [False, True], ids=["filesystem", "git"])
def test_directory_link_retargeting_invalidates_receipt(workspace, capsys, git):
    forge, planning, target = workspace
    for name in ("first", "second"):
        (target / name).mkdir()
    link = target / "linked"
    symlink(link, "first")
    inventory(target, git)
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    link.unlink()
    symlink(link, "second")
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("git", [False, True], ids=["filesystem", "git"])
def test_directory_links_do_not_hide_ordinary_source_changes(workspace, capsys, git):
    forge, planning, target = workspace
    source = target / "src/source.py"
    source.parent.mkdir()
    source.write_text("value = 1\n")
    symlink(target / "linked", "src")
    inventory(target, git)
    verify(workspace, capsys)
    source.write_text("value = 2\n")
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("git", [False, True], ids=["filesystem", "git"])
def test_external_directory_link_binds_entry_without_reading_contents(workspace, capsys, tmp_path, monkeypatch, git):
    forge, planning, target = workspace
    external = tmp_path / "external"
    external.mkdir()
    secret = external / "private.txt"
    secret.write_text("not a repository input")
    symlink(target / "linked", external)
    inventory(target, git)
    read_bytes, read_text = Path.read_bytes, Path.read_text

    def guarded(read):
        def check(path, *args, **kwargs):
            assert not path.resolve().is_relative_to(external)
            return read(path, *args, **kwargs)
        return check

    monkeypatch.setattr(Path, "read_bytes", guarded(read_bytes))
    monkeypatch.setattr(Path, "read_text", guarded(read_text))
    open_file = forge.mutable_inputs.os.open

    def guarded_open(path, *args, **kwargs):
        assert not Path(path).resolve().is_relative_to(external)
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(forge.mutable_inputs.os, "open", guarded_open)
    verify(workspace, capsys)
    secret.write_text("external edits remain outside the source snapshot")
    assert forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("git", [False, True], ids=["filesystem", "git"])
@pytest.mark.parametrize("external", [False, True], ids=["internal-target", "external-target"])
def test_dangling_directory_link_becoming_resolvable_invalidates_receipt(workspace, capsys, tmp_path, git, external):
    forge, planning, target = workspace
    destination = (tmp_path if external else target) / "missing"
    symlink(target / "linked", destination if external else destination.name)
    inventory(target, git)
    verify(workspace, capsys)
    destination.mkdir()
    assert not forge.verification.integration_report(planning, target)["success"]
    verify(workspace, capsys)
    assert forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("git", [False, True], ids=["filesystem", "git"])
def test_regular_file_links_keep_content_and_containment_checks(workspace, capsys, tmp_path, git):
    forge, planning, target = workspace
    source = target / ".venv/source.py"
    source.parent.mkdir()
    source.write_text("value = 1\n")
    (target / ".gitignore").write_text(".venv/\n")
    symlink(target / "linked.py", ".venv/source.py", directory=False)
    inventory(target, git)
    verify(workspace, capsys)
    source.write_text("value = 2\n")
    assert not forge.verification.integration_report(planning, target)["success"]
    external = tmp_path / "external.py"
    external.write_text("must not be read")
    (target / "linked.py").unlink()
    symlink(target / "linked.py", external, directory=False)
    with pytest.raises(ValueError, match="stay within the target directory"):
        forge.mutable_inputs.verification_snapshot(planning, target)
