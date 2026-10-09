"""Independent preparation cases; imported only by the execution test aggregator.

Synthetic fixtures never qualify a real provider. Parent-run tests use temporary
Git repositories; authoring this file does not execute them.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_inventory import fingerprint, inventory
from coding_trial_suite import _freeze_study, _prepare_attempt, _read_study, prepare_suite, run_suite
from trial_suite_fixtures import digest, link, make_suite, regular_entries, write_files


BUDGET = {"timeout_seconds": 900, "output_bytes": 8388608}
PYTHON = Path(sys.executable).resolve()


def save_manifest(path, suite):
    path.write_text(json.dumps(suite, indent=2) + "\n", encoding="utf-8")


def bytes_at(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def read_reference(root, reference):
    assert set(reference) == {"path", "sha256"}
    path = Path(reference["path"])
    path = path if path.is_absolute() else root / path
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    return path, raw


def git(workspace, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "--no-replace-objects", *args], cwd=workspace,
                          env=env, capture_output=True, text=True, check=True, timeout=10).stdout.strip()


def schedule(*arms):
    return [{"position": index, "task": "normalize", "arm": arm, "repeat": 0,
             "budget": dict(BUDGET)} for index, arm in enumerate(arms)]


def marked_suite(tmp_path):
    path, suite = make_suite(tmp_path / "curator")
    marker = tmp_path / "writer-must-not-start"
    suite["host"]["fixture_argv"] = ["{python}", "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('writer started')"]
    save_manifest(path, suite)
    return path, suite, marker


class TestSuitePreparation:
    @pytest.mark.parametrize("arm", ["alpha", "bravo", "charlie"])
    def test_prepare_freezes_neutral_inputs_and_initial_git_without_running_writer(self, tmp_path, arm):
        path, suite, marker = marked_suite(tmp_path)
        (path.parent / "unreferenced-private.txt").write_text("must not be copied")
        before = bytes_at(path.parent)
        trial = tmp_path / "trial"
        record = prepare_suite(trial, path, "normalize", arm, assessor_python=PYTHON)
        assert record == json.loads((trial / "trial.json").read_text())
        assert record["schema"] == "coding-trial-attempt/v1" and record["status"] == "prepared"
        assert record["runner"] is record["candidate"] is record["native"] is record["error"] is None
        assert record["assessments"] == [] and record["qualify_loading"] is False
        assert record["scheduled_position"] == 0 and record["budget"] == BUDGET
        assert record["identity"] == {
            "suite_sha256": digest(suite), "task": "normalize", "arm": arm,
            "baseline_sha256": suite["tasks"]["normalize"]["source"]["baseline_sha256"],
            "configuration_sha256": digest(suite["arms"][arm]["configuration"])}
        assert set(record["admission_gates"]) == {"environment", "task", "isolation", "loading"}
        study_path, study_raw = read_reference(trial, record["study"])
        assert study_path == tmp_path / "trial.study" / "study.json"
        study = json.loads(study_raw)
        assert study["schema"] == "coding-trial-study/v1"
        assert study["schedule"] == schedule(arm) and study["qualify_loading"] is False
        assert study["manifest"] == {"path": path.name,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                     "suite_sha256": digest(suite)}
        frozen = Path(study["suite_root"])
        assert frozen == study_path.parent / "suite"
        for resource in (path.name, "brief.md", "checks/oracle.py", "checks/support.py",
                         "checks/worker.py", "entries/alpha.md", "entries/bravo.md",
                         "entries/charlie.md", "export/backend/app/service.py",
                         "products/bravo/SKILL.md", "products/charlie/SKILL.md"):
            assert (frozen / resource).read_bytes() == (path.parent / resource).read_bytes()
        assert not (frozen / "unreferenced-private.txt").exists()
        assert study["input_resources"] == sorted(set(study["input_resources"]))
        _, inventory_raw = read_reference(study_path.parent, study["input_inventory"])
        assert fingerprint(json.loads(inventory_raw)) == study["input_inventory_sha256"]
        assert study["controller"]["executable"] == str(PYTHON)
        assert study["controller"]["executable_sha256"] == hashlib.sha256(PYTHON.read_bytes()).hexdigest()
        assert isinstance(study["controller"]["version"], str) and study["controller"]["version"]
        roots = record["roots"]
        assert roots["native_runtime"] is None
        assert any(frozen.is_relative_to(Path(root)) for root in roots["private"])
        for name in ("workspace", "product", "public", "generated"):
            assert Path(roots[name]) == (trial / name).resolve()
            assert not frozen.is_relative_to(Path(roots[name]))
        product = suite["arms"][arm]["product"]
        assert fingerprint(inventory(Path(roots["product"]))) == (
            fingerprint({}) if product is None else product["inventory_sha256"])
        workspace = Path(roots["workspace"])
        for name, raw in bytes_at(path.parent / "export").items():
            assert (workspace / name).read_bytes() == raw
        assert not (workspace / "checks").exists()
        assert git(workspace, "rev-parse", "HEAD") == record["initial_git"]["commit"]
        assert git(workspace, "rev-parse", "HEAD^{tree}") == record["initial_git"]["tree"]
        read_reference(trial, record["initial_git"]["history"])
        assert git(workspace, "status", "--porcelain") == ""
        assert not marker.exists() and bytes_at(path.parent) == before

    @pytest.mark.parametrize("location", ["trial", "trial.study"])
    @pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
    def test_existing_trial_or_sibling_is_preserved_before_other_creation(self, tmp_path, location, kind):
        path, _, marker = marked_suite(tmp_path)
        occupied = tmp_path / location
        target = tmp_path / "user-owned"
        target.mkdir()
        (target / "keep").write_text("user bytes")
        if kind == "directory":
            occupied.mkdir()
            (occupied / "keep").write_text("existing trial bytes")
        elif kind == "file":
            occupied.write_text("existing file bytes")
        else:
            link(occupied, str(target), directory=True)
        before = bytes_at(path.parent)
        with pytest.raises(ValueError, match="input-exists"):
            prepare_suite(tmp_path / "trial", path, "normalize", "alpha", assessor_python=PYTHON)
        other = tmp_path / ("trial.study" if location == "trial" else "trial")
        assert not other.exists() and not marker.exists()
        assert (target / "keep").read_text() == "user bytes"
        if kind == "symlink":
            assert occupied.is_symlink()
        elif kind == "file":
            assert occupied.read_text() == "existing file bytes"
        else:
            assert bytes_at(occupied) == {"keep": b"existing trial bytes"}
        assert bytes_at(path.parent) == before

    @pytest.mark.parametrize("status", ["prepared", "running", "failed", "completed"])
    def test_run_never_adopts_an_existing_attempt_of_any_state(self, tmp_path, status):
        path, _, marker = marked_suite(tmp_path)
        trial = tmp_path / "trial"
        trial.mkdir()
        original = json.dumps({"schema": "coding-trial-attempt/v1", "status": status}).encode()
        (trial / "trial.json").write_bytes(original)
        with pytest.raises(ValueError, match="input-exists"):
            run_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
        assert bytes_at(trial) == {"trial.json": original}
        assert not (tmp_path / "trial.study").exists() and not marker.exists()

    @pytest.mark.parametrize("task,arm", [("missing", "alpha"), ("normalize", "missing")])
    def test_unknown_selection_creates_neither_attempt_nor_study(self, tmp_path, task, arm):
        path, _, marker = marked_suite(tmp_path)
        before = bytes_at(path.parent)
        with pytest.raises(ValueError, match="suite-invalid"):
            prepare_suite(tmp_path / "trial", path, task, arm, assessor_python=PYTHON)
        assert not (tmp_path / "trial").exists() and not (tmp_path / "trial.study").exists()
        assert bytes_at(path.parent) == before and not marker.exists()

    def test_changed_baseline_is_rejected_before_destination_creation(self, tmp_path):
        path, _, marker = marked_suite(tmp_path)
        (path.parent / "export/settings.toml").write_text("changed since the manifest was bound")
        before = bytes_at(path.parent)
        with pytest.raises(ValueError, match="suite-invalid"):
            prepare_suite(tmp_path / "trial", path, "normalize", "alpha", assessor_python=PYTHON)
        assert not (tmp_path / "trial").exists() and not (tmp_path / "trial.study").exists()
        assert bytes_at(path.parent) == before and not marker.exists()

    def test_original_manifest_basename_is_retained(self, tmp_path):
        path, _, _ = marked_suite(tmp_path)
        renamed = path.with_name("chosen-neutral-input.json")
        path.rename(renamed)
        record = prepare_suite(tmp_path / "trial", renamed, "normalize", "charlie", assessor_python=PYTHON)
        study = json.loads(Path(record["study"]["path"]).read_text())
        assert study["manifest"]["path"] == renamed.name
        assert (Path(study["suite_root"]) / renamed.name).read_bytes() == renamed.read_bytes()
        assert not (Path(study["suite_root"]) / "suite.json").exists()

    def test_matrix_composes_one_frozen_study_and_ignores_later_original_edits(self, tmp_path):
        path, _, marker = marked_suite(tmp_path)
        ordered = schedule("charlie", "alpha", "bravo")
        study_ref = _freeze_study(tmp_path / "shared-study", path, ordered, PYTHON)
        study_path = Path(study_ref["path"])
        assert study_path == tmp_path / "shared-study/study.json"
        frozen = bytes_at(study_path.parent)
        (path.parent / "brief.md").write_text("later curator edit")
        (path.parent / "products/charlie/SKILL.md").write_text("later product edit")
        path.write_text("not the frozen manifest")
        records = [_prepare_attempt(tmp_path / f"attempt-{position}", study_ref, position,
                                   status="prepared", qualify_loading=False, auth_file=None)
                   for position in (0, 1)]
        assert records[0]["study"] == records[1]["study"]
        assert [record["identity"]["arm"] for record in records] == ["charlie", "alpha"]
        assert [record["scheduled_position"] for record in records] == [0, 1]
        assert json.loads(study_path.read_text())["schedule"] == ordered
        assert bytes_at(study_path.parent) == frozen and not marker.exists()

    @pytest.mark.parametrize("fault", ["duplicate-position", "unknown-arm", "unequal-budget"])
    def test_entire_schedule_is_validated_before_freezing_any_inputs(self, tmp_path, fault):
        path, _, _ = marked_suite(tmp_path)
        ordered = schedule("alpha", "bravo", "charlie")
        if fault == "duplicate-position":
            ordered[-1]["position"] = 0
        elif fault == "unknown-arm":
            ordered[-1]["arm"] = "unknown"
        else:
            ordered[-1]["budget"]["timeout_seconds"] = 901
        destination = tmp_path / "study"
        with pytest.raises(ValueError):
            _freeze_study(destination, path, ordered, PYTHON)
        assert not destination.exists()

    @pytest.mark.parametrize("frozen,requested", [(False, True), (True, False)])
    def test_attempt_cannot_override_frozen_loading_authorization(self, tmp_path, frozen, requested):
        path, _, marker = marked_suite(tmp_path)
        study_ref = _freeze_study(tmp_path / "study", path, schedule("alpha"), PYTHON,
                                   qualify_loading=frozen)
        study_path = Path(study_ref["path"])
        before = bytes_at(study_path.parent)
        trial = tmp_path / "trial"
        with pytest.raises(ValueError):
            _prepare_attempt(trial, study_ref, 0, status="prepared",
                             qualify_loading=requested, auth_file=None)
        assert not trial.exists() and not marker.exists()
        assert bytes_at(study_path.parent) == before

    def test_shared_study_read_is_pure_and_returns_the_frozen_manifest(self, tmp_path, monkeypatch):
        path, suite, _ = marked_suite(tmp_path)
        record = prepare_suite(tmp_path / "trial", path, "normalize", "alpha", assessor_python=PYTHON)
        root = Path(record["study"]["path"]).parent
        before = bytes_at(root)
        path.write_text("mutable source is no longer consulted")
        def forbidden(*args, **kwargs):
            pytest.fail("Reading frozen study identities must not run a process")
        monkeypatch.setattr(subprocess, "run", forbidden)
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        study, validated, suite_root = _read_study(record["study"])
        assert validated == suite and suite_root == Path(study["suite_root"])
        assert study == json.loads(Path(record["study"]["path"]).read_text())
        assert bytes_at(root) == before

    @pytest.mark.parametrize("changed", ["oracle", "saved-inventory", "frozen-evaluator"])
    def test_frozen_identity_drift_blocks_new_attempt_before_creation(self, tmp_path, changed):
        path, _, _ = marked_suite(tmp_path)
        study_ref = _freeze_study(tmp_path / "study", path, schedule("alpha"), PYTHON)
        study_path = Path(study_ref["path"])
        study = json.loads(study_path.read_text())
        target = {"oracle": Path(study["suite_root"]) / "checks/oracle.py",
                  "saved-inventory": study_path.parent / study["input_inventory"]["path"],
                  "frozen-evaluator": Path(study["evaluator"]["root"]) / "tools/coding_trial_runner.py"}[changed]
        target.write_bytes(target.read_bytes() + b"\n# changed after freeze\n")
        before = bytes_at(study_path.parent)
        with pytest.raises(ValueError, match="input-drift"):
            _prepare_attempt(tmp_path / "trial", study_ref, 0, status="prepared",
                             qualify_loading=False, auth_file=None)
        assert not (tmp_path / "trial").exists() and bytes_at(study_path.parent) == before

    def test_plain_arm_retains_baseline_material_named_like_forge_artifacts(self, tmp_path):
        path, suite, _ = marked_suite(tmp_path)
        export = path.parent / "export"
        write_files(export, {".planning/retained.txt": "This is baseline source, not workflow output.\n"})
        task = suite["tasks"]["normalize"]
        task["scope"]["config"].append(".planning")
        suite["arms"]["bravo"]["artifacts"] = [".forge-output"]
        names = tuple(name for name in bytes_at(export))
        task["source"]["baseline_sha256"] = digest(regular_entries(export, names))
        save_manifest(path, suite)
        record = prepare_suite(tmp_path / "trial", path, "normalize", "alpha", assessor_python=PYTHON)
        workspace = Path(record["roots"]["workspace"])
        assert (workspace / ".planning/retained.txt").read_bytes() == (export / ".planning/retained.txt").read_bytes()
        assert ".planning/retained.txt" in git(workspace, "ls-files").splitlines()
        assert git(workspace, "status", "--porcelain") == ""

    def test_synthetic_resources_may_use_native_only_attempt_namespace(self, tmp_path):
        path, suite, _ = marked_suite(tmp_path)
        write_files(path.parent, {"_attempts/brief.md": "Ordinary declared synthetic resource.\n"})
        suite["tasks"]["normalize"]["brief"] = "_attempts/brief.md"
        save_manifest(path, suite)
        record = prepare_suite(tmp_path / "trial", path, "normalize", "alpha", assessor_python=PYTHON)
        study = json.loads(Path(record["study"]["path"]).read_text())
        assert (Path(study["suite_root"]) / "_attempts/brief.md").read_bytes() == (
            path.parent / "_attempts/brief.md").read_bytes()
