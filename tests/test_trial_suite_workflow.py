"""Workflow identity controls; fictional Docker observations prove no isolation.

Only the final class executes the trusted evaluator helper on temporary legacy
fixtures when the parent runs these tests. No provider or real Docker is used.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from coding_trial_assessment import assess_suite
from coding_trial_inventory import fingerprint, inventory
from coding_trial_suite import _read_study, prepare_suite, run_suite
from coding_trial_runner import run_suite_writer
import coding_trial_isolation as isolation
import coding_trial_workflow as workflow
from test_coding_trials import trials, verify_integration, write_cleanup, write_workflow
from trial_suite_assessment_cases import (
    RoleBoundary, assessment_fixture, codes, last_assessment, load_json,
    load_ref, process_result, review_for, save_review,
)
from trial_suite_fixtures import link, write_files
from trial_suite_prepare_cases import PYTHON, bytes_at, save_manifest


ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_COUNT": "3",
    "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
    "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false",
    "GIT_CONFIG_KEY_2": "core.untrackedCache", "GIT_CONFIG_VALUE_2": "false",
}
GENERATED = "backend/app/build/observed.json"
PLAN = "docs/forge/feature"


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def summary(trial, verdict):
    return load_ref(trial, verdict["behavior"])["workflow"]


def workflow_inputs(tmp_path, *, depth="standard", configured=True, oracle="passed", arm="bravo"):
    path, suite = assessment_fixture(tmp_path, oracle=oracle)
    if arm == "bravo":
        suite["arms"][arm]["artifacts"] = [PLAN]
        suite["arms"][arm]["configuration"] = {"depth": depth} if configured else {"opaque": "preserved"}
        # A tempting product-owned checker must never become evaluator authority.
        payload = path.parent / suite["arms"][arm]["product"]["payload"]
        write_files(payload, {"scripts/zagrosi_skills.py": "raise AssertionError('product checker executed')\n"})
        suite["arms"][arm]["product"]["inventory_sha256"] = fingerprint(inventory(payload))
    suite["tasks"]["normalize"]["scope"]["generated"] += ["backend/app/build"]
    save_manifest(path, suite)
    return path, suite


def write_fixture_plan(workspace, *, depth="standard", shape="valid"):
    """Declaration-shaped artifacts for the explicit executor double, not valid proof."""
    write_files(workspace, {
        PLAN + "/sections/index.md": '<!-- FORGE_META\n' + json.dumps({
            "artifact_type": "compact_plan", "depth_mode": depth}) + '\nEND_FORGE_META -->\n',
        PLAN + "/implementation/receipt.json": '{"fixture":"not real workflow evidence"}\n',
        GENERATED: '{"observed":"must be visible during validation"}\n',
    })
    if shape == "missing":
        (workspace / PLAN / "sections/index.md").unlink()
    elif shape == "rival":
        write_files(workspace, {PLAN + "/another/codex-plan.md": "A rival declared plan.\n"})
    elif shape == "link":
        marker = workspace / PLAN / "sections/index.md"
        marker.rename(marker.with_name("actual.md"))
        link(marker, "actual.md")


class WorkflowBoundary:
    """Existing owned-execution seam only; retained responses are explicitly fake."""
    def __init__(self, monkeypatch, *, depth="standard", configured=True, fault=None):
        self.depth, self.configured, self.fault, self.calls = depth, configured, fault, []
        monkeypatch.setattr(workflow, "run_owned", self)

    def __call__(self, profile, layout, argv, *, cwd, env, prompt, timeout, output_limit, evidence_dir):
        directory = Path(evidence_dir)
        assert directory.name in {"exposure", "validation"}
        stage = directory.name
        assert directory.parent.name == "workflow-validation" and directory.parent.parent.name == "assessor"
        trial = directory.parent.parent.parent
        attempt = load_json(trial / "trial.json")
        study, suite, suite_root = _read_study(attempt["study"])
        workspace, evaluator = trial / "workspace", Path(study["evaluator"]["root"])
        namespace = "/workspace"  # Synthetic writers with Docker use the container namespace too.
        assert profile == load_json(suite_root / suite["host"]["isolation"]["profile"])
        assert layout["network"] == "none" and layout["user"] == profile["user"]
        expected_mounts = [
            {"source": str(workspace), "target": namespace, "read_only": True},
            {"source": str(trial / "assessor/git/metadata"), "target": namespace + "/.git", "read_only": True},
            {"source": str(evaluator), "target": "/checks", "read_only": True},
        ]
        assert len(layout["mounts"]) == 3
        assert {mount["target"]: mount for mount in layout["mounts"]} == {
            mount["target"]: mount for mount in expected_mounts}
        assert str(cwd) == namespace and env == ENV and prompt is None
        assert (workspace / GENERATED).read_text() == '{"observed":"must be visible during validation"}\n'
        assert not (trial / "assessor/candidate").exists(), "Validate before filtered projection/mountpoints"
        assert fingerprint(inventory(workspace / ".git")) == fingerprint(inventory(trial / "assessor/git/metadata"))
        assert directory.is_dir() and not any(directory.iterdir())
        before = (bytes_at(workspace), bytes_at(evaluator))
        process = process_result()
        if stage == "exposure":
            assert argv[:4] == ["/usr/local/bin/python3", "-I", "-B", "-c"] and len(argv) == 6
            assert isinstance(argv[4], str) and argv[4].strip()
            assert (timeout, output_limit) == (15, 65536)
            spec = json.loads(argv[-1])
            assert set(spec) == {"marker", "marker_sha256", "private", "readonly", "nonce"}
            marker = workspace / PLAN / "sections/index.md"
            assert spec["marker"] == namespace + "/" + PLAN + "/sections/index.md"
            assert spec["marker_sha256"] == hashlib.sha256(marker.read_bytes()).hexdigest()
            assert spec["readonly"] == [namespace, "/checks"] and re.fullmatch(r"[0-9a-f]{32}", spec["nonce"])
            private = Path(spec["private"])
            assert private.is_file() and private.is_relative_to(trial / "assessor")
            assert all(not private.is_relative_to(Path(mount["source"])) for mount in layout["mounts"])
            observed = {key: True for key in ("task_read", "readonly_roots", "private_read", "private_write",
                                              "child_private", "root_write", "product_read_policy", "docker_socket")}
            observed.update(executable_sha256="b" * 64, git_version="git version fictional-test-only",
                            network_interfaces=[{"name": "lo", "flags": 9}],
                            network_connection={"succeeded": False, "errno": 101})
            if self.fault == "exposure":
                observed["private_read"] = False
            process["stdout"] = json.dumps(observed)
        else:
            expected = ["/usr/local/bin/python3", "-I", "-B", "/checks/tools/coding_trial_evidence.py",
                        "/checks", namespace]
            if self.configured:
                expected.append(self.depth)
            assert argv == expected + ["--planning-dir", namespace + "/" + PLAN]
            assert (timeout, output_limit) == (60, 12000)
            report = {"success": True, "admission_success": True, "sections_recorded_complete": True,
                      "selected_depth": self.depth, "planning_depth": self.depth,
                      "planning_dir": namespace + "/" + PLAN, "reasons": []}
            if self.fault in {"success", "admission_success", "sections_recorded_complete"}:
                report[self.fault] = False
            if self.fault == "nonzero":
                process["returncode"] = 7
            elif self.fault == "boolean-exit":
                process["returncode"] = False
            elif self.fault in {"timed_out", "stdout_truncated", "stderr_truncated"}:
                process[self.fault] = True
            elif self.fault == "termination_error":
                process["termination_error"] = "Fictional teardown failure"
            process["stdout"] = json.dumps(report)
        process["stdout_bytes"] = len(process["stdout"].encode())
        container = f"{len(self.calls) + 1:064x}"
        config = directory / "docker-config"
        config.mkdir()
        raw = directory / "docker-000.json"
        lifecycle = {"status": "unverified" if self.fault == "cleanup" else "verified",
                     "reason": "Explicit workflow test double; no container exists", "evidence": [str(raw)]}
        result = {"process": process, "container_id": container, "lifecycle": lifecycle,
                  "identity": {"executable_sha256": "7" * 64, "docker_version": "fictional-docker",
                               "image_id": "sha256:" + "8" * 64}}
        save(raw, {"argv": [profile["docker_executable"], "--host", profile["endpoint"], "--config", str(config),
                             "start", "--attach", "--interactive", container],
                   "environment": {}, "process": deepcopy(process),
                   "started_at": "2026-10-09T10:00:00Z", "ended_at": "2026-10-09T10:00:00.001000Z"})
        save(directory / "lifecycle.json", {"container_id": container, "owner": f"{len(self.calls) + 1:032x}", **lifecycle})
        assert before == (bytes_at(workspace), bytes_at(evaluator))
        self.calls.append({"stage": stage, "argv": list(argv), "result": deepcopy(result), "raw": raw})
        if stage == "validation" and self.fault == "raw-start":
            retained = load_json(raw)
            retained["process"]["returncode"] = 7
            save(raw, retained)
        if stage == "validation" and self.fault == "source-drift":
            (workspace / "backend/app/service.py").write_text("# Changed during workflow observation\n")
        return result


def run_workflow(tmp_path, monkeypatch, *, depth="standard", configured=True, fault=None, shape="valid",
                 oracle="passed", arm="bravo"):
    path, _ = workflow_inputs(tmp_path, depth=depth, configured=configured, oracle=oracle, arm=arm)
    owned = WorkflowBoundary(monkeypatch, depth=depth, configured=configured, fault=fault)
    writer = (lambda root: write_fixture_plan(root, depth=depth, shape=shape)) if arm == "bravo" else None
    roles = RoleBoundary(writer=writer)
    monkeypatch.setattr(isolation, "execute_isolated", roles)
    trial = tmp_path / "trial"
    run_suite(trial, path, "normalize", arm, assessor_python=PYTHON)
    return trial, owned, roles, last_assessment(trial)


class TestSuiteWorkflow:
    @pytest.mark.parametrize("depth,configured", [("lean", False), ("standard", True), ("deep", False)])
    def test_declared_custom_plan_sees_complete_original_tree_with_only_fixed_readonly_grants(
            self, tmp_path, monkeypatch, depth, configured):
        trial, owned, _, verdict = run_workflow(tmp_path, monkeypatch, depth=depth, configured=configured)
        value = summary(trial, verdict)
        assert value["status"] == "passed" and value["evidence"]
        assert [call["stage"] for call in owned.calls] == ["exposure", "validation"]
        for reference in value["evidence"]:
            assert not Path(reference["path"]).is_absolute()
            load_ref(trial, reference)
        assert not (trial / "assessor/candidate" / GENERATED).exists()
        assert (trial / "assessor/workflow" / PLAN / "implementation/receipt.json").read_text() == (
            '{"fixture":"not real workflow evidence"}\n')

    @pytest.mark.parametrize("shape", ["missing", "rival", "link"])
    def test_missing_rival_or_linked_declared_markers_cannot_select_a_validator(self, tmp_path, monkeypatch, shape):
        trial, owned, _, verdict = run_workflow(tmp_path, monkeypatch, shape=shape)
        assert summary(trial, verdict)["status"] == "failed"
        assert owned.calls == []

    @pytest.mark.parametrize("fault", ["exposure", "cleanup", "nonzero", "boolean-exit", "timed_out",
                                      "stdout_truncated", "stderr_truncated", "termination_error",
                                      "success", "admission_success", "sections_recorded_complete"])
    def test_only_complete_observation_and_complete_workflow_can_pass(self, tmp_path, monkeypatch, fault):
        trial, owned, _, verdict = run_workflow(tmp_path, monkeypatch, fault=fault)
        assert summary(trial, verdict)["status"] == "failed"
        assert owned.calls and all(call["raw"].is_file() for call in owned.calls)
        if fault in {"exposure", "cleanup"}:
            assert [call["stage"] for call in owned.calls] == ["exposure"]

    def test_source_drift_during_validation_cannot_become_a_frozen_delivery(self, tmp_path, monkeypatch):
        with pytest.raises(ValueError, match="candidate-drift"):
            run_workflow(tmp_path, monkeypatch, fault="source-drift")
        assert load_json(tmp_path / "trial/trial.json")["status"] == "failed"
        assert (tmp_path / "trial/assessor/workflow-validation/validation/docker-000.json").is_file()

    def test_unverified_writer_cleanup_never_starts_workflow_observation(self, tmp_path, monkeypatch):
        path, _ = workflow_inputs(tmp_path)
        owned = WorkflowBoundary(monkeypatch)
        roles = RoleBoundary(writer=write_fixture_plan, cleanup="unverified")
        monkeypatch.setattr(isolation, "execute_isolated", roles)
        trial = tmp_path / "trial"
        run_suite(trial, path, "normalize", "bravo", assessor_python=PYTHON)
        attempt = load_json(trial / "trial.json")
        runner = load_ref(trial, attempt["runner"])
        assert runner["process"]["returncode"] == 0
        assert runner["isolation"]["lifecycle"]["status"] == "unverified"
        assert owned.calls == []
        assert summary(trial, last_assessment(trial))["status"] == "failed"

    def test_passing_returned_report_cannot_override_failed_retained_start(self, tmp_path, monkeypatch):
        trial, owned, _, verdict = run_workflow(tmp_path, monkeypatch, fault="raw-start")
        assert [call["stage"] for call in owned.calls] == ["exposure", "validation"]
        assert owned.calls[-1]["result"]["process"]["returncode"] == 0
        assert load_json(owned.calls[-1]["raw"])["process"]["returncode"] == 7
        assert summary(trial, verdict)["status"] == "failed"

    def test_review_reuses_frozen_workflow_after_original_workspace_is_removed(self, tmp_path, monkeypatch):
        trial, owned, roles, first = run_workflow(tmp_path, monkeypatch)
        original = summary(trial, first)
        review = save_review(trial, review_for(trial, first))
        shutil.rmtree(trial / "workspace")
        def forbidden(*args, **kwargs):
            pytest.fail("Review may not rerun workflow, checks or the writer")
        monkeypatch.setattr(workflow, "run_owned", forbidden)
        monkeypatch.setattr(isolation, "execute_isolated", forbidden)
        reviewed = assess_suite(trial, review=review)
        assert reviewed["common_quality"] == "passed" and reviewed["synthetic_validation"] == "passed"
        assert summary(trial, reviewed) == original and reviewed["behavior"] == first["behavior"]
        assert len(owned.calls) == 2 and [call["role"] for call in roles.calls] == ["writer", "native"]

    @pytest.mark.parametrize("target", ["artifact", "raw-result"])
    def test_frozen_workflow_or_raw_evidence_drift_blocks_reuse(self, tmp_path, monkeypatch, target):
        trial, owned, _, first = run_workflow(tmp_path, monkeypatch)
        review = save_review(trial, review_for(trial, first))
        changed = (trial / "assessor/workflow" / PLAN / "implementation/receipt.json"
                   if target == "artifact" else owned.calls[-1]["raw"])
        changed.write_text(changed.read_text() + " ")
        result = assess_suite(trial, review=review)
        assert result["common_quality"] == "failed"
        assert codes(result) & {"candidate-drift", "receipt-invalid", "input-drift"}
        assert len(owned.calls) == 2

    @pytest.mark.parametrize("oracle,fault,quality", [("failed", None, "failed"), ("passed", "success", "passed")])
    def test_workflow_neither_rescues_nor_overrides_common_quality(self, tmp_path, monkeypatch, oracle, fault, quality):
        trial, _, _, first = run_workflow(tmp_path, monkeypatch, oracle=oracle, fault=fault)
        result = assess_suite(trial, review=save_review(trial, review_for(trial, first)))
        assert result["common_quality"] == quality
        assert summary(trial, result)["status"] == ("passed" if fault is None else "failed")
        assert result["study_eligible"] is False and result["acceptance"] == "failed"

    @pytest.mark.parametrize("arm,status", [("alpha", "not_applicable"), ("charlie", "unmeasured")])
    def test_nonforge_workflow_modes_do_not_start_validation(self, tmp_path, monkeypatch, arm, status):
        trial, owned, _, verdict = run_workflow(tmp_path, monkeypatch, arm=arm)
        assert summary(trial, verdict)["status"] == status and owned.calls == []


class TestSuiteWriterNamespace:
    @pytest.mark.parametrize("docker", [False, True])
    def test_public_writer_uses_its_execution_namespace_independently_of_synthetic_purpose(
            self, tmp_path, monkeypatch, docker):
        path, suite = assessment_fixture(tmp_path)
        suite["host"]["fixture_argv"] += ["{workspace}", "{product}"]
        if not docker:
            suite["host"]["isolation"] = None
        save_manifest(path, suite)
        trial = tmp_path / "trial"
        attempt = prepare_suite(trial, path, "normalize", "bravo", assessor_python=PYTHON)
        _, frozen, root = _read_study(attempt["study"])
        if docker:
            roles = RoleBoundary()
            monkeypatch.setattr(isolation, "execute_isolated", roles)
            calls = roles.calls
        else:
            calls = []
            def host_execute(argv, workspace, **kwargs):
                calls.append({"argv": list(argv), "cwd": str(workspace), "prompt": kwargs["prompt"]})
                return process_result()
            monkeypatch.setattr("coding_trial_process.execute", host_execute)
        result = run_suite_writer(frozen, root, "normalize", "bravo", attempt["roots"], trial / "private/writer")
        workspace = "/workspace" if docker else attempt["roots"]["workspace"]
        product = "/product" if docker else attempt["roots"]["product"]
        assert frozen["purpose"] == "synthetic" and len(calls) == 1
        assert calls[0]["cwd"] == workspace
        assert calls[0]["argv"][-2:] == [workspace, product]
        assert f"task files in {workspace}." in calls[0]["prompt"]
        assert result["command"] == calls[0]["argv"]
        assert result["isolation"]["adapter"] == ("docker-v1" if docker else None)


def helper_fixture(tmp_path, depth="standard", *, custom=True, generated=False):
    trial = tmp_path / "legacy"
    trials.prepare(trial, "cleanup", depth=depth)
    write_cleanup(trial)
    workspace = trial / "workspace"
    planning = workspace / (PLAN if custom else ".planning/feature")
    write_workflow(workspace, depth, planning=planning)
    if generated:
        section = planning / "sections/section-01-invoice.md"
        section.write_text(section.read_text().replace("- src/ledger.py\n", "- src\n").replace(
            "found the invoice module", "found `src/ledger.py`"))
        write_files(workspace, {"src/build/receipt-input.json": '{"generated":"observed by owned directory"}\n'})
        with (workspace / ".git/info/exclude").open("a") as stream:
            stream.write("/src/build/\n")
        verify_integration(workspace, planning=planning)
    return workspace, planning


def run_helper(workspace, planning=None, depth=None):
    argv = [str(PYTHON), "-I", "-B", str(ROOT / "tools/coding_trial_evidence.py"), str(ROOT), str(workspace)]
    if depth is not None:
        argv.append(depth)
    if planning is not None:
        argv += ["--planning-dir", str(planning)]
    result = trials.execute(argv, workspace, timeout=60, output_limit=12000)
    assert not result["timed_out"] and not result["stdout_truncated"] and not result["stderr_truncated"], result
    return result, json.loads(result["stdout"])


class TestWorkflowHelper:
    @pytest.mark.parametrize("depth", ["lean", "standard", "deep"])
    def test_real_helper_accepts_declared_custom_plan_at_actual_depth(self, tmp_path, depth):
        workspace, planning = helper_fixture(tmp_path, depth)
        before = bytes_at(workspace)
        result, report = run_helper(workspace, planning)
        assert result["returncode"] == 0 and report["success"] and report["admission_success"]
        assert report["sections_recorded_complete"] and report["planning_depth"] == report["selected_depth"] == depth
        assert report["planning_dir"] == str(planning) and bytes_at(workspace) == before

    def test_legacy_three_argument_call_keeps_nested_selection_and_depth_mismatch(self, tmp_path):
        workspace, planning = helper_fixture(tmp_path, custom=False)
        result, report = run_helper(workspace, depth="standard")
        assert result["returncode"] == 0 and report["sections_recorded_complete"]
        assert report["planning_dir"] == str(planning)
        failed, mismatch = run_helper(workspace, depth="deep")
        assert failed["returncode"] != 0 and mismatch["success"] is False

    @pytest.mark.parametrize("changed", ["source", "receipt", "generated"])
    def test_real_path_bound_receipt_rejects_changed_observed_material(self, tmp_path, changed):
        workspace, planning = helper_fixture(tmp_path, generated=True)
        positive, report = run_helper(workspace, planning)
        assert positive["returncode"] == 0 and report["sections_recorded_complete"], report
        if changed == "source":
            with (workspace / "src/ledger.py").open("a") as stream:
                stream.write("\n# Changed after verification.\n")
        elif changed == "generated":
            (workspace / "src/build/receipt-input.json").unlink()
        else:
            state = planning / "implementation/zagrosi_implement_state.json"
            value = load_json(state)
            value["completed_sections"].clear()
            save(state, value)
        _, failed = run_helper(workspace, planning)
        assert not (failed.get("success") is True and failed.get("admission_success") is True
                    and failed.get("sections_recorded_complete") is True), failed
