"""Frozen assessment/lineage controls; Docker results here are explicit test doubles.

Parent-run tests execute only a tiny trusted private oracle and temporary Git.
The role executor is replaced; no real container/provider qualification is claimed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_isolation as isolation
from coding_trial_assessment import assess_suite
from coding_trial_suite import run_suite
from trial_suite_fixtures import make_suite, write_files
from trial_suite_prepare_cases import PYTHON, bytes_at, git, read_reference, save_manifest


ORACLE = '''import json,os,sys
from pathlib import Path
descriptor=json.loads(Path(sys.argv[1]).read_text())
assert sys.flags.isolated and sys.dont_write_bytecode
assert os.environ.get('FORGE_TEST_PRIVATE_AMBIENT') is None
assert Path.cwd()!=Path(descriptor['candidate']['path'])
scenario=json.loads(Path(__file__).with_name('scenario.json').read_text())
checks=[{'id':'normalize-prefix','status':'passed','detail':'Private feature check.'},
        {'id':'normal-version','status':'passed','detail':'Private preservation check.'}]
if scenario['oracle']=='missing-id': checks.pop()
if scenario['oracle']=='duplicate-id': checks.append(dict(checks[0]))
if scenario['oracle']=='failed': checks[0]['status']='failed'
receipt={'schema':'coding-trial-oracle/v1','task':descriptor['identity']['task'],
         'candidate_sha256':descriptor['candidate']['assessed_sha256'],'checks':checks}
if scenario['oracle']=='wrong-candidate': receipt['candidate_sha256']='0'*64
Path(sys.argv[2]).write_text(json.dumps(receipt)+'\\n')
print('Trusted synthetic oracle completed.')
'''


def load_json(path):
    return json.loads(Path(path).read_text())


def load_ref(trial, reference):
    return json.loads(read_reference(trial, reference)[1])


def last_assessment(trial):
    attempt = load_json(trial / "trial.json")
    assert attempt["assessments"], "A terminal assessment must be persisted"
    return load_ref(trial, attempt["assessments"][-1])


def codes(verdict):
    return {error["code"] for error in verdict["errors"]}


def process_result(*, code=0, stdout="", timed_out=False):
    return {"returncode": code, "seconds": .001, "timed_out": timed_out,
            "stdout": stdout, "stderr": "", "stdout_bytes": len(stdout.encode()),
            "stderr_bytes": 0, "stdout_truncated": False, "stderr_truncated": False}


def assessment_fixture(tmp_path, *, oracle="passed", local_commits="allow", cleanup_required=False):
    path, suite = make_suite(tmp_path / "curator")
    image = "example.invalid/synthetic-assessment@sha256:" + "a" * 64
    profile = {"schema": "coding-trial-docker-profile/v1",
               "docker_executable": str((tmp_path / "never-run-docker").resolve()),
               "context": "fictional-test-only", "endpoint": "unix:///never-used/docker.sock",
               "server_id": "fictional-test-only", "server_version": "fictional-v1",
               "image_digest": image, "platform": "linux/amd64", "user": "1000:1000",
               "limits": {"memory_bytes": 67108864, "pids": 32, "cpus": .5},
               "network": "none", "runtime_paths": ["/usr/local/bin/python3"],
               "network_checks": ["network-denied"]}
    write_files(path.parent, {"profile.json": json.dumps(profile), "checks/oracle.py": ORACLE,
                             "checks/scenario.json": json.dumps({"oracle": oracle})})
    suite["host"]["isolation"] = {"adapter": "docker-v1", "profile": "profile.json",
                                  "image_digest": image, "probe_receipt": None}
    task = suite["tasks"]["normalize"]
    task["local_commits"] = local_commits
    task["cleanup_required"] = cleanup_required
    task["checks"]["oracle"]["support"] = ["checks/scenario.json"]
    task["checks"]["oracle"]["argv"] = ["{python}", "{entry}", "{assessment}", "{receipt}"]
    task["checks"]["oracle"]["timeout_seconds"] = 5
    save_manifest(path, suite)
    return path, suite


class RoleBoundary:
    """Only the existing isolated-execution seam is doubled; all records say so."""
    def __init__(self, *, writer=None, worker=None, writer_code=0, native_code=0, cleanup="verified"):
        self.writer = writer
        self.worker = worker
        self.writer_code = writer_code
        self.native_code = native_code
        self.cleanup = cleanup
        self.calls = []

    def __call__(self, suite, suite_root, layout, argv, *, roots, cwd, env, prompt,
                 timeout, output_limit, evidence_dir, auth_file=None, cancel_event=None):
        assert auth_file is None, "Synthetic assessment cannot receive credentials"
        assert layout == isolation.derive_layout(suite, suite_root, layout["task"], layout["arm"],
                                                roots, role=layout["role"])
        assert not Path(evidence_dir).exists()
        Path(evidence_dir).mkdir()
        self.calls.append({"role": layout["role"], "roots": deepcopy(roots), "layout": deepcopy(layout),
                           "argv": list(argv), "cwd": cwd, "env": dict(env), "prompt": prompt,
                           "timeout": timeout, "output_limit": output_limit,
                           "evidence_dir": str(evidence_dir)})
        workspace = Path(roots["workspace"])
        if layout["role"] == "writer":
            (workspace / "backend/app/service.py").write_text(
                "from packaging.version import Version\n\ndef normalize(value):\n"
                "    return str(Version(value.removeprefix('v')))\n")
            if self.writer is not None:
                self.writer(workspace)
            process = process_result(code=self.writer_code)
        elif layout["role"] == "native":
            process = process_result(code=self.native_code)
            assert all(mount["read_only"] for mount in layout["mounts"]
                       if mount["target"] in ("/workspace", "/checks"))
            assert not any(mount["target"] == "/product" for mount in layout["mounts"])
        elif layout["role"] == "worker" and self.worker is not None:
            assert isinstance(prompt, str) and prompt.endswith("\n")
            process = self.worker(json.loads(prompt), roots, cancel_event)
        else:
            pytest.fail("The assessment-only oracle never asks for a worker")
        qualification = Path(evidence_dir) / "qualification.json"
        qualification.write_text(json.dumps({"fixture": "logical boundary double; no real qualification"}))
        lifecycle_path = Path(evidence_dir) / "lifecycle.json"
        lifecycle = {"status": self.cleanup, "reason": "Synthetic executor double only",
                     "evidence": [str(lifecycle_path)]}
        lifecycle_path.write_text(json.dumps({"fixture": True, "status": self.cleanup}))
        return {"process": process, "isolation": {
            "adapter": "docker-v1", "status": "passed" if self.cleanup == "verified" else "failed",
            "qualification_seconds": .002,
            "qualification": {"path": str(qualification),
                              "sha256": hashlib.sha256(qualification.read_bytes()).hexdigest()},
            "container_id": "f" * 64, "lifecycle": lifecycle}}


def run_case(tmp_path, monkeypatch, *, boundary=None, **fixture_options):
    path, suite = assessment_fixture(tmp_path, **fixture_options)
    boundary = boundary or RoleBoundary()
    monkeypatch.setenv("FORGE_TEST_PRIVATE_AMBIENT", "test-only ambient marker")
    monkeypatch.setattr(isolation, "execute_isolated", boundary)
    trial = tmp_path / "trial"
    result = run_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
    assert result == load_json(trial / "trial.json")
    return trial, suite, boundary, last_assessment(trial)


def review_for(trial, verdict, *, cleanup=None):
    attempt = load_json(trial / "trial.json")
    return {"schema": "coding-trial-review/v1", "block": "normalize", "reviewer": "Independent test curator",
            "independent": True, "preferred": ["C001"], "rationale": "One reviewed synthetic candidate.",
            "candidates": {"C001": {
                "baseline_sha256": attempt["identity"]["baseline_sha256"],
                "candidate_sha256": attempt["candidate"]["assessed_sha256"],
                "assessment_sha256": verdict["behavior"]["sha256"], "verdict": "pass", "findings": [],
                "criteria": {name: "backend/app/service.py: normalize remains a single readable operation."
                             for name in ("readability", "cohesion", "duplication", "regressions")},
                "cleanup": cleanup}}}


def save_review(trial, value, name="review.json"):
    path = trial.parent / name
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


class TestSuiteAssessment:
    def test_real_private_oracle_and_native_records_bind_the_frozen_delivery(self, tmp_path, monkeypatch):
        trial, _, boundary, verdict = run_case(tmp_path, monkeypatch)
        attempt = load_json(trial / "trial.json")
        assert attempt["status"] == "completed" and attempt["runner"] is not None
        assert [call["role"] for call in boundary.calls] == ["writer", "native"]
        assert verdict["kind"] == "check" and verdict["number"] == 1
        assert verdict["common_quality"] == "pending" and "review-pending" in codes(verdict)
        assert verdict["acceptance"] != "passed" and verdict["study_eligible"] is False
        behavior = load_ref(trial, verdict["behavior"])
        assert behavior["schema"] == "coding-trial-behavior/v1"
        assert behavior["identity"] == attempt["identity"]
        assert behavior["observations"] == []
        assert all(check["status"] == "passed" for check in behavior["checks"].values())
        oracle = load_ref(trial, behavior["oracle"]["receipt"])
        assert oracle["candidate_sha256"] == attempt["candidate"]["assessed_sha256"]
        assert {check["id"] for check in oracle["checks"]} == {"normalize-prefix", "normal-version"}
        descriptor = load_ref(trial, behavior["descriptor"])
        assert descriptor["candidate"]["path"] == attempt["candidate"]["path"] or (
            Path(descriptor["candidate"]["path"]) == trial / attempt["candidate"]["path"])
        assert descriptor["controller"]["executable"] == str(PYTHON)
        client = descriptor["observation_client"]
        assert Path(client["path"]).is_absolute()
        assert hashlib.sha256(Path(client["path"]).read_bytes()).hexdigest() == client["sha256"]
        assert Path(client["path"]).is_relative_to(Path(load_ref(trial, attempt["study"])["evaluator"]["root"]))
        assert len(behavior["native"]) == 1 and behavior["native"][0]["id"] == "native"
        native = load_ref(trial, behavior["native"][0]["record"])
        assert native["process"]["returncode"] == 0
        basis_path, basis = read_reference(trial, verdict["attempt_basis"])
        assert basis_path.name == "attempt-basis.json" and json.loads(basis)["assessments"] == []
        assert behavior["attempt_basis"] == verdict["attempt_basis"]
        assert verdict["attempt_basis"]["sha256"] != hashlib.sha256((trial / "trial.json").read_bytes()).hexdigest()

    @pytest.mark.parametrize("oracle", ["missing-id", "duplicate-id", "wrong-candidate", "failed"])
    def test_zero_oracle_exit_cannot_rescue_invalid_or_failed_private_checks(self, tmp_path, monkeypatch, oracle):
        trial, _, _, verdict = run_case(tmp_path, monkeypatch, oracle=oracle)
        assert verdict["common_quality"] == "failed" and verdict["acceptance"] != "passed"
        behavior = load_ref(trial, verdict["behavior"])
        if oracle == "failed":
            receipt = load_ref(trial, behavior["oracle"]["receipt"])
            assert any(check["status"] == "failed" for check in receipt["checks"])
        else:
            assert behavior["errors"], "Invalid private receipts must retain the actual failure"
        review = save_review(trial, review_for(trial, verdict))
        reviewed = assess_suite(trial, review=review)
        assert reviewed["common_quality"] == "failed" and reviewed["synthetic_validation"] != "passed"

    @pytest.mark.parametrize("failure", ["native", "writer", "cleanup"])
    def test_independent_execution_failures_survive_passing_oracle_and_review(self, tmp_path, monkeypatch, failure):
        boundary = RoleBoundary(native_code=7 if failure == "native" else 0,
                                writer_code=9 if failure == "writer" else 0,
                                cleanup="failed" if failure == "cleanup" else "verified")
        trial, _, _, verdict = run_case(tmp_path, monkeypatch, boundary=boundary)
        assert verdict["acceptance"] != "passed" and verdict["synthetic_validation"] != "passed"
        if verdict["behavior"] is not None:
            reviewed = assess_suite(trial, review=save_review(trial, review_for(trial, verdict)))
            assert reviewed["synthetic_validation"] != "passed"

    @pytest.mark.parametrize("change,expected", [("new-root-file", "scope-invalid"),
                                                ("protected-file", "scope-invalid"),
                                                ("dependency-lock", "dependency-invalid")])
    def test_full_inventory_audit_sees_changes_outside_assessed_projection(self, tmp_path, monkeypatch, change, expected):
        def mutate(workspace):
            target = {"new-root-file": "unclassified.txt", "protected-file": "AGENTS.md",
                      "dependency-lock": "deps.lock"}[change]
            (workspace / target).write_text("unadmitted candidate change\n")
        trial, _, _, verdict = run_case(tmp_path, monkeypatch, boundary=RoleBoundary(writer=mutate))
        behavior = None if verdict["behavior"] is None else load_ref(trial, verdict["behavior"])
        actual = codes(verdict) | (set() if behavior is None else codes(behavior))
        assert expected in actual and verdict["common_quality"] == "failed"

    @pytest.mark.parametrize("policy,action,valid", [("allow", "worktree", True),
        ("allow", "commit", True), ("forbid", "worktree", True), ("forbid", "commit", False),
        ("allow", "amend", False), ("allow", "remove-git", False)])
    def test_terminal_git_policy_distinguishes_valid_delivery_from_rewritten_provenance(self, tmp_path, monkeypatch, policy, action, valid):
        def mutate(workspace):
            if action in ("commit", "amend"):
                git(workspace, "add", "--", "backend/app/service.py")
                options = ["--amend"] if action == "amend" else []
                git(workspace, "commit", "--no-gpg-sign", *options, "-m", "Candidate delivery")
            elif action == "remove-git":
                shutil.rmtree(workspace / ".git")
        trial, _, _, verdict = run_case(tmp_path, monkeypatch, local_commits=policy,
                                       boundary=RoleBoundary(writer=mutate))
        if valid:
            assert verdict["common_quality"] == "pending"
            behavior = load_ref(trial, verdict["behavior"])
            assert behavior["checks"]["git"]["status"] == "passed"
        else:
            assert verdict["common_quality"] == "failed"
            behavior = None if verdict["behavior"] is None else load_ref(trial, verdict["behavior"])
            assert "scope-invalid" in (codes(verdict) | (set() if behavior is None else codes(behavior)))

    def test_recheck_uses_frozen_delivery_and_git_after_original_workspace_changes(self, tmp_path, monkeypatch):
        trial, _, boundary, first = run_case(tmp_path, monkeypatch)
        attempt = load_json(trial / "trial.json")
        candidate = Path(attempt["candidate"]["path"])
        candidate = candidate if candidate.is_absolute() else trial / candidate
        before = bytes_at(candidate)
        workspace = trial / "workspace"
        (workspace / "backend/app/service.py").write_text("raise RuntimeError('later unrelated edit')\n")
        shutil.rmtree(workspace / ".git")
        second = assess_suite(trial)
        assert second["number"] == 2 and second["common_quality"] == "pending"
        assert bytes_at(candidate) == before
        assert [call["role"] for call in boundary.calls] == ["writer", "native", "native"]
        assert all(Path(call["roots"]["workspace"]) == candidate for call in boundary.calls[1:])
        assert first["behavior"] != second["behavior"]

    def test_review_only_reuses_behavior_without_execution_and_keeps_hash_lineage(self, tmp_path, monkeypatch):
        trial, _, boundary, first = run_case(tmp_path, monkeypatch)
        original = bytes_at(trial / "assessor/assessments/000001")
        before = (trial / "trial.json").read_bytes()
        calls = deepcopy(boundary.calls)
        def forbidden(*args, **kwargs):
            pytest.fail("Applying a material review must not repeat execution or qualification")
        monkeypatch.setattr(isolation, "execute_isolated", forbidden)
        import coding_trial_process
        monkeypatch.setattr(coding_trial_process, "execute", forbidden)
        reviewed = assess_suite(trial, review=save_review(trial, review_for(trial, first)))
        assert reviewed["kind"] == "review" and reviewed["number"] == 2
        assert reviewed["behavior"] == first["behavior"]
        assert read_reference(trial, reviewed["attempt_basis"])[1] == before
        assert reviewed["common_quality"] == "passed" and reviewed["synthetic_validation"] == "passed"
        assert reviewed["acceptance"] != "passed" and reviewed["study_eligible"] is False
        assert bytes_at(trial / "assessor/assessments/000001") == original and boundary.calls == calls

    def test_fresh_check_makes_prior_material_review_stale_even_for_same_candidate(self, tmp_path, monkeypatch):
        trial, _, _, first = run_case(tmp_path, monkeypatch)
        review = save_review(trial, review_for(trial, first))
        assert assess_suite(trial, review=review)["synthetic_validation"] == "passed"
        second = assess_suite(trial)
        assert second["behavior"]["sha256"] != first["behavior"]["sha256"]
        stale = assess_suite(trial, review=review)
        assert "review-stale" in codes(stale) and stale["synthetic_validation"] != "passed"
        fresh = save_review(trial, review_for(trial, second), name="current-review.json")
        assert assess_suite(trial, review=fresh)["synthetic_validation"] == "passed"

    @pytest.mark.parametrize("target", ["candidate", "oracle", "behavior"])
    def test_review_cannot_rescue_drift_in_frozen_inputs_delivery_or_behavior(self, tmp_path, monkeypatch, target):
        trial, _, _, first = run_case(tmp_path, monkeypatch)
        review = save_review(trial, review_for(trial, first))
        attempt = load_json(trial / "trial.json")
        if target == "candidate":
            path = Path(attempt["candidate"]["path"])
            changed = (path if path.is_absolute() else trial / path) / "backend/app/service.py"
        elif target == "oracle":
            changed = Path(load_ref(trial, attempt["study"])["suite_root"]) / "checks/oracle.py"
        else:
            changed = read_reference(trial, first["behavior"])[0]
        changed.write_bytes(changed.read_bytes() + b"\nchanged after receipt\n")
        result = assess_suite(trial, review=review)
        assert result["synthetic_validation"] != "passed" and result["errors"]

    def test_missing_assessment_profile_preserves_writer_and_persists_explicit_gate_failure(self, tmp_path):
        path, suite = make_suite(tmp_path / "curator")
        suite["host"]["fixture_argv"] = ["{python}", "-c",
            "from pathlib import Path; Path('backend/app/service.py').write_text('def normalize(value): return value\\n')"]
        save_manifest(path, suite)
        trial = tmp_path / "trial"
        with pytest.raises(ValueError, match="unsupported-profile"):
            run_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
        attempt = load_json(trial / "trial.json")
        assert attempt["status"] == "completed"
        assert load_ref(trial, attempt["runner"])["process"]["returncode"] == 0
        verdict = last_assessment(trial)
        assert verdict["behavior"] is None and "unsupported-profile" in codes(verdict)
        assert verdict["synthetic_validation"] != "passed" and verdict["study_eligible"] is False
