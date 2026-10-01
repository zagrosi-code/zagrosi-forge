"""Directory-owned work can be recorded and resumed without following directory links."""

import pytest

from forge_test_helpers import run_cmd
from test_compact_plan import SECTION, make_plan
from test_mutable_state_safety import record_args, setup_args


def directory_workspace(tmp_path, depth="lean", existing=True):
    planning = make_plan(tmp_path / "plan", depth)
    section = planning / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text().replace("```text\nsrc/labels.py\n", "```text\nsrc\n"))
    target = tmp_path / "target"
    target.mkdir()
    source = target / "src/labels.py"
    if existing:
        source.parent.mkdir()
        source.write_text("def normalize(value): return value.strip()\n")
    run_cmd(*setup_args(planning, target))
    return planning, target, source


def progress(planning):
    return run_cmd("implement-progress", "--planning-dir", str(planning),
                   "--section", SECTION, "--stage", "green")


def resumed(planning):
    return run_cmd("next-section", "--planning-dir", str(planning))["resume"]


@pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
def test_owned_directory_supports_progress_and_completion(tmp_path, depth):
    planning, _, _ = directory_workspace(tmp_path, depth)

    event = progress(planning)["event"]

    assert "snapshot_error" not in event
    assert event["snapshot"]["code"]
    assert resumed(planning)["evidence_current"] is True
    result = run_cmd(*record_args(planning))
    assert result["recorded"] is True
    assert result["completed_sections"] == [SECTION]


@pytest.mark.parametrize("change", ["edit", "add", "remove"])
def test_directory_descendant_changes_invalidate_resume_evidence(tmp_path, change):
    planning, _, source = directory_workspace(tmp_path)
    progress(planning)
    assert resumed(planning)["evidence_current"] is True
    if change == "edit":
        source.write_text("def normalize(value): return value.lower()\n")
    elif change == "add":
        (source.parent / "exports.py").write_text("exports = []\n")
    else:
        source.unlink()

    result = resumed(planning)

    assert result["evidence_current"] is False
    assert any(value.startswith("code:") for value in result["changed_inputs"])


def test_initially_absent_owned_directory_is_observed_when_created(tmp_path):
    planning, _, source = directory_workspace(tmp_path, existing=False)
    progress(planning)
    assert resumed(planning)["evidence_current"] is True
    source.parent.mkdir()
    source.write_text("def normalize(value): return value.strip()\n")

    assert resumed(planning)["evidence_current"] is False
    assert "snapshot_error" not in progress(planning)["event"]
    assert run_cmd(*record_args(planning))["recorded"] is True


def test_unowned_source_changes_do_not_invalidate_directory_progress(tmp_path):
    planning, target, _ = directory_workspace(tmp_path)
    outside = target / "unrelated.py"
    outside.write_text("value = 1\n")
    progress(planning)
    outside.write_text("value = 2\n")

    assert resumed(planning)["evidence_current"] is True


def test_owned_directory_records_link_identity_without_following_directory_links(tmp_path):
    planning, _, source = directory_workspace(tmp_path)
    outside = tmp_path / "external"
    outside.mkdir()
    external_file = outside / "private.txt"
    external_file.write_text("unrelated external content\n")
    link = source.parent / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")

    assert "snapshot_error" not in progress(planning)["event"]
    external_file.write_text("changed external content\n")
    assert resumed(planning)["evidence_current"] is True
    link.unlink()
    link.symlink_to(tmp_path / "absent", target_is_directory=True)
    assert resumed(planning)["evidence_current"] is False
