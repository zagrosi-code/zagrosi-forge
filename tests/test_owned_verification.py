"""Captured verification binds explicit ownership even when Git ignores it."""

import os
import subprocess
import sys

import pytest

from test_compact_plan import SECTION
from test_verification_receipts import workspace, invoke, record_args, verify_args
from test_verification_repositories import git, another_commit


@pytest.fixture
def owned_workspace(workspace):
    _, planning, target = workspace
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    (target / ".gitignore").write_text("private/\ncache/\n")
    (target / "private").mkdir()
    (target / "private/labels.py").write_text("def normalize(value): return value.strip()\n")
    (target / "check.py").write_text(
        "import runpy\n"
        "normalize = runpy.run_path('private/labels.py')['normalize']\n"
        "assert normalize(' Ada  Lovelace ') == 'Ada  Lovelace'\n"
    )
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("src/labels.py", "private/labels.py")
                       .replace("tests/test_labels.py", "check.py"))
    return workspace


def select_ownership(workspace, owned):
    _, planning, _ = workspace
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("```text\nprivate/labels.py\n", f"```text\n{owned}\n"))


def capture(workspace, capsys, *command):
    code, payload = invoke(workspace, capsys, *verify_args(
        workspace, "--section", SECTION, "--integration", "--", *(command or (sys.executable, "check.py"))))
    assert code == 0, payload
    return payload


def mutate(target, change):
    source = target / "private/labels.py"
    if change == "edit":
        source.write_text("def normalize(value): return value.lower()\n")
    elif change == "add":
        (source.parent / "exports.py").write_text("enabled = True\n")
    elif change == "remove":
        source.unlink()
    elif change == "mode":
        source.chmod(source.stat().st_mode ^ 0o100)


@pytest.mark.parametrize("owned,change", [
    ("private/labels.py", "edit"), ("private/labels.py", "remove"), ("private/labels.py", "mode"),
    ("private", "edit"), ("private", "add"), ("private", "remove"), ("private", "mode"),
])
def test_ignored_owned_changes_invalidate_section_and_integration(owned_workspace, capsys, owned, change):
    if change == "mode" and os.name == "nt":
        pytest.skip("Windows does not expose the POSIX executable mode bit")
    forge, planning, target = owned_workspace
    select_ownership(owned_workspace, owned)
    verified = capture(owned_workspace, capsys)

    mutate(target, change)

    code, result = invoke(owned_workspace, capsys, *record_args(
        owned_workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 1, result
    assert "inputs changed" in result["error"].lower()
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("owned", ["private/future.py", "private/future"])
def test_creation_of_missing_ignored_owned_input_invalidates_receipts(owned_workspace, capsys, owned):
    forge, planning, target = owned_workspace
    select_ownership(owned_workspace, owned)
    verified = capture(owned_workspace, capsys)
    created = target / owned
    if created.suffix:
        created.write_text("value = 1\n")
    else:
        created.mkdir()
        (created / "source.py").write_text("value = 1\n")

    code, result = invoke(owned_workspace, capsys, *record_args(
        owned_workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 1, result
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("owned", ["private/labels.py", "private"])
def test_postflight_rejects_ignored_owned_change_after_recording(owned_workspace, capsys, owned):
    _, planning, target = owned_workspace
    select_ownership(owned_workspace, owned)
    verified = capture(owned_workspace, capsys)
    code, result = invoke(owned_workspace, capsys, *record_args(
        owned_workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 0, result

    mutate(target, "edit")
    check = subprocess.run([sys.executable, "check.py"], cwd=target, capture_output=True)
    assert check.returncode != 0
    code, result = invoke(owned_workspace, capsys, "postflight", "--phase", "implement",
                          "--planning-dir", str(planning), "--target-dir", str(target), "--strict")
    assert code == 1, result
    assert "integration-verification" in result["blocking_gates"]


@pytest.mark.parametrize("owned", ["private/labels.py", "private"])
def test_verifier_cannot_change_ignored_owned_source_and_pass(owned_workspace, capsys, owned):
    select_ownership(owned_workspace, owned)
    code, result = invoke(owned_workspace, capsys, *verify_args(
        owned_workspace, "--section", SECTION, "--integration", "--", sys.executable, "-c",
        "import runpy; from pathlib import Path; runpy.run_path('check.py'); "
        "Path('private/labels.py').write_text('def normalize(value): return value.lower()\\n')"))
    assert code == 1, result
    assert result["outcome"] == "failed"
    assert result["exit_code"] == 0


@pytest.mark.parametrize("owned", ["private/labels.py", "private"])
def test_unrelated_ignored_cache_keeps_owned_verification_current(owned_workspace, capsys, owned):
    forge, planning, target = owned_workspace
    select_ownership(owned_workspace, owned)
    verified = capture(owned_workspace, capsys)
    cache = target / "cache"
    cache.mkdir()
    (cache / "derived.py").write_text("cache = 1\n")
    (cache / "derived.py").write_text("cache = 2\n")

    assert forge.verification.integration_report(planning, target)["success"]
    code, result = invoke(owned_workspace, capsys, *record_args(
        owned_workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 0, result
    code, result = invoke(owned_workspace, capsys, "postflight", "--phase", "implement",
                          "--planning-dir", str(planning), "--target-dir", str(target), "--strict")
    assert code == 0, result


@pytest.mark.parametrize("change", ["add", "remove", "mode"])
def test_empty_owned_descendant_changes_invalidate_receipts(owned_workspace, capsys, change):
    if change == "mode" and os.name == "nt":
        pytest.skip("Windows does not expose POSIX directory permission bits")
    forge, planning, target = owned_workspace
    select_ownership(owned_workspace, "private")
    directory = target / "private/ready"
    if change != "add":
        directory.mkdir(mode=0o750)
        directory.chmod(0o750)
    assertion = ("assert not ready.exists()" if change == "add" else
                 "assert ready.is_dir()" if change == "remove" else
                 "assert ready.stat().st_mode & 0o777 == 0o750")
    command = (sys.executable, "-c", "from pathlib import Path; ready = Path('private/ready'); " + assertion)
    verified = capture(owned_workspace, capsys, *command)

    if change == "add":
        directory.mkdir()
    elif change == "remove":
        directory.rmdir()
    else:
        directory.chmod(0o700)
    assert subprocess.run(command, cwd=target, capture_output=True).returncode != 0
    code, result = invoke(owned_workspace, capsys, *record_args(
        owned_workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 1, result
    assert not forge.verification.integration_report(planning, target)["success"]


@pytest.mark.parametrize("owned", ["private", "private/component"])
def test_ignored_owned_repository_preserves_gitlink_identity(owned_workspace, capsys, owned):
    forge, planning, target = owned_workspace
    nested = target / "private/component"
    nested.mkdir()
    git(nested, "init", "-q")
    git(nested, "-c", "user.name=Forge", "-c", "user.email=forge@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "fixture")
    first, second = another_commit(nested)
    git(nested, "update-index", "--add", "--cacheinfo", f"160000,{first},dependency")
    select_ownership(owned_workspace, owned)
    command = (sys.executable, "-c", "import subprocess; "
               "entry = subprocess.check_output(['git', '-C', 'private/component', 'ls-files', '--stage', 'dependency']); "
               f"assert entry.split()[1].decode() == {first!r}")
    verified = capture(owned_workspace, capsys, *command)

    git(nested, "update-index", "--cacheinfo", f"160000,{second},dependency")
    assert subprocess.run(command, cwd=target, capture_output=True).returncode != 0
    code, result = invoke(owned_workspace, capsys, *record_args(
        owned_workspace, "--verification-receipt", verified["receipt_path"]))
    assert code == 1, result
    assert not forge.verification.integration_report(planning, target)["success"]


def test_first_capture_inside_owned_tree_excludes_only_generated_containers(owned_workspace, capsys):
    forge, planning, target = owned_workspace
    select_ownership(owned_workspace, "private")
    planning = planning.rename(target / "private/plan")
    active = forge, planning, target
    assert not (planning / "implementation").exists()

    capture(active, capsys)
    assert forge.verification.integration_report(planning, target)["success"]
    scores = planning / ".forge/scores"
    scores.mkdir(parents=True)
    (scores / "generated.json").write_text('{"score": 100}\n')
    assert forge.verification.integration_report(planning, target)["success"]

    (planning / "implementation/source.py").write_text("source = True\n")
    assert not forge.verification.integration_report(planning, target)["success"]
    capture(active, capsys)
    assert forge.verification.integration_report(planning, target)["success"]

    (planning / "implementation/ordinary").mkdir()
    assert not forge.verification.integration_report(planning, target)["success"]
