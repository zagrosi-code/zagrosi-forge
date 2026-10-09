"""Source-review regressions for the public evaluator entry-point identity.

REQ-001 makes suite dispatch/controller/exit policy evaluator behavior; REQ-004
and the frozen-study contract bind that behavior for later saved assessments.
Only an owned source copy is mutated. Authoring did not import or execute code.
"""
from pathlib import Path
import subprocess

import pytest

from trial_suite_prepare_cases import PYTHON, marked_suite
from coding_trial_inventory import copy_snapshot, inventory
import coding_trial_study as study_api
import coding_trial_suite as suite_api


@pytest.mark.parametrize("changed", [None, "tools/coding_trials.py", "tools/coding_trial_suite_cli.py"])
def test_saved_study_binds_public_evaluator_entries_without_reexecution(tmp_path, monkeypatch, changed):
    original = study_api.ROOT
    entries = ("tools/coding_trials.py", "tools/coding_trial_suite_cli.py")
    original_bytes = {name: (original / name).read_bytes() for name in entries}
    owned = tmp_path / "evaluator-source"
    selected = tuple(sorted(set(study_api.EVALUATOR_SOURCES) | set(entries)))
    copy_snapshot(original, owned, inventory(original, included=selected))
    # Redirect only the source-root boundary; creation/readback stay genuine.
    monkeypatch.setattr(study_api, "ROOT", owned)
    monkeypatch.setattr(suite_api, "ROOT", owned)
    manifest, expected, marker = marked_suite(tmp_path)
    attempt = suite_api.prepare_suite(tmp_path / "trial", manifest, "normalize", "alpha",
                                      assessor_python=PYTHON)
    if changed is not None:
        target = owned / changed
        assert target.is_relative_to(owned) and not target.is_relative_to(original)
        target.write_bytes(target.read_bytes() + b"\n# Entry-point policy changed after study freeze.\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Saved source identity readback must not execute processes")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    if changed is None:
        record, actual, root = study_api._read_study(attempt["study"])
        assert actual == expected and root == Path(record["suite_root"])
    else:
        with pytest.raises(ValueError, match="^input-drift:"):
            study_api._read_study(attempt["study"])
    assert not marker.exists()
    assert {name: (original / name).read_bytes() for name in entries} == original_bytes
