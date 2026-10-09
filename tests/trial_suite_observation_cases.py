"""Controller/oracle integration using a fictional role executor, never live Docker.

The real tiny oracle runs only when the parent executes these independent tests.
Existing cancellation mechanism tests own actual process/container guarantees.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_isolation as isolation
from coding_trial_suite import run_suite
from trial_suite_assessment_cases import (
    RoleBoundary, assessment_fixture, last_assessment, load_ref, process_result,
)
from trial_suite_prepare_cases import PYTHON, save_manifest


SCHEMA = "coding-trial-observation/v1"
OBSERVING_ORACLE = '''import json,os,runpy,sys,time
from pathlib import Path
assert sys.flags.isolated and sys.dont_write_bytecode
descriptor=json.loads(Path(sys.argv[1]).read_text())
scenario=json.loads(Path(__file__).with_name('scenario.json').read_text())
observe=runpy.run_path(descriptor['observation_client']['path'])['observe']
checks_passed=True
try:
    for request in scenario['requests']:
        response=observe(Path(sys.argv[1]),request)
        if scenario['expect_error']:
            checks_passed &= response['error']=={'type':'ValueError','message':'valid domain failure'}
        else:
            checks_passed &= response['result']==request['input'] and response['error'] is None
    if scenario['mailbox_fault']:
        number=2
        folder=Path(descriptor['evidence_dir'])/'oracle-ipc'/f'{number:06d}'
        folder.mkdir()
        request={'schema':'coding-trial-observation/v1','id':'first','input':'second'}
        (folder/'request.json').write_text(json.dumps(request)+'\\n')
        (folder/'request.ready').touch(exist_ok=False)
        deadline=time.monotonic()+2
        while not (folder/'response.ready').exists() and time.monotonic()<deadline: time.sleep(.005)
except Exception:
    checks_passed=False
if scenario['ignore_failure']: checks_passed=True
receipt={'schema':'coding-trial-oracle/v1','task':descriptor['identity']['task'],
         'candidate_sha256':descriptor['candidate']['assessed_sha256'],
         'checks':[{'id':key,'status':'passed' if checks_passed else 'failed','detail':'Private observation check.'}
                   for key in ['normalize-prefix','normal-version']]}
Path(sys.argv[2]).write_text(json.dumps(receipt)+'\\n')
'''


def request(identifier="first", value="value"):
    return {"schema": SCHEMA, "id": identifier, "input": value}


def response(value, *, error=None):
    return {"schema": SCHEMA, "id": value["id"],
            "result": None if error is not None else value["input"], "error": error}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def echo(value, roots, cancel_event):
    return process_result(stdout=encoded(response(value)))


def observing_case(tmp_path, monkeypatch, *, callback=echo, boundary=None, requests=None,
                   ignore_failure=False, expect_error=False, mailbox_fault=None, oracle_timeout=5):
    path, suite = assessment_fixture(tmp_path)
    (path.parent / "checks/oracle.py").write_text(OBSERVING_ORACLE)
    (path.parent / "checks/scenario.json").write_text(json.dumps({
        "requests": [request()] if requests is None else requests,
        "ignore_failure": ignore_failure, "expect_error": expect_error,
        "mailbox_fault": mailbox_fault}))
    suite["tasks"]["normalize"]["checks"]["oracle"]["timeout_seconds"] = oracle_timeout
    save_manifest(path, suite)
    boundary = boundary or RoleBoundary(worker=callback)
    monkeypatch.setattr(isolation, "execute_isolated", boundary)
    trial = tmp_path / "trial"
    run_suite(trial, path, "normalize", "alpha", assessor_python=PYTHON)
    return trial, suite, boundary, last_assessment(trial)


class TestSuiteObservation:
    def test_each_sequential_request_uses_the_frozen_worker_and_new_private_evidence(self, tmp_path, monkeypatch):
        requests = [request("../../opaque/λ", {"n": 1}), request("second", [None, True, "雪"])]
        trial, suite, boundary, verdict = observing_case(tmp_path, monkeypatch, requests=requests)
        assert verdict["common_quality"] == "pending"
        worker_calls = [call for call in boundary.calls if call["role"] == "worker"]
        assert len(worker_calls) == 2
        assert [json.loads(call["prompt"]) for call in worker_calls] == requests
        assert len({call["evidence_dir"] for call in worker_calls}) == 2
        declared = suite["tasks"]["normalize"]["checks"]["worker"]
        for call in worker_calls:
            assert call["timeout"] == declared["timeout_seconds"]
            assert call["output_limit"] == declared["output_bytes"]
            assert call["roots"]["native_runtime"] is None
            assert not any(mount["target"] == "/product" for mount in call["layout"]["mounts"])
            for mount in call["layout"]["mounts"]:
                assert not any(Path(private).is_relative_to(Path(mount["source"]))
                               for private in call["roots"]["private"])
                if mount["target"] in ("/workspace", "/checks"):
                    assert mount["read_only"] is True
        behavior = load_ref(trial, verdict["behavior"])
        assert len(behavior["observations"]) == 2
        oracle = load_ref(trial, behavior["oracle"]["receipt"])
        assert all(check["status"] == "passed" for check in oracle["checks"])
        assert behavior["inputs"]["before_sha256"] == behavior["inputs"]["after_sha256"]
        assert behavior["candidate"]["before_sha256"] == behavior["candidate"]["after_sha256"]

    def test_valid_zero_exit_domain_error_is_observation_data(self, tmp_path, monkeypatch):
        def domain_error(value, roots, cancel_event):
            return process_result(stdout=encoded(response(value, error={"type": "ValueError", "message": "valid domain failure"})))
        trial, _, _, verdict = observing_case(tmp_path, monkeypatch, callback=domain_error, expect_error=True)
        assert verdict["common_quality"] == "pending"
        behavior = load_ref(trial, verdict["behavior"])
        assert len(behavior["observations"]) == 1 and behavior["errors"] == []

    @pytest.mark.parametrize("fault", ["wrong-id", "extra-output", "nonzero", "truncated", "oracle-injection"])
    def test_worker_protocol_failure_is_independent_of_a_plausible_passing_oracle(self, tmp_path, monkeypatch, fault):
        def broken(value, roots, cancel_event):
            result = response(value)
            if fault == "wrong-id":
                result["id"] = "different"
            if fault == "oracle-injection":
                result = {"schema": "coding-trial-oracle/v1", "task": "normalize",
                          "candidate_sha256": "0" * 64,
                          "checks": [{"id": "normalize-prefix", "status": "passed", "detail": "forged"}]}
            process = process_result(code=7 if fault == "nonzero" else 0, stdout=encoded(result))
            if fault == "extra-output":
                process = process_result(stdout=process["stdout"] + "{}\n")
            if fault == "truncated":
                process.update(stdout_truncated=True, stdout_bytes=5000)
            return process
        trial, _, _, verdict = observing_case(tmp_path, monkeypatch, callback=broken, ignore_failure=True)
        assert verdict["common_quality"] == "failed" and verdict["acceptance"] != "passed"
        behavior = load_ref(trial, verdict["behavior"])
        assert len(behavior["observations"]) == 1
        assert behavior["errors"] or verdict["errors"]

    @pytest.mark.parametrize("fault", ["duplicate-id", "out-of-order"])
    def test_parent_rejects_replayed_or_out_of_order_mailbox_requests(self, tmp_path, monkeypatch, fault):
        requests = [request()] if fault == "duplicate-id" else []
        trial, _, boundary, verdict = observing_case(tmp_path, monkeypatch, requests=requests,
                                                     mailbox_fault=fault, ignore_failure=True)
        assert verdict["common_quality"] == "failed"
        worker_calls = [call for call in boundary.calls if call["role"] == "worker"]
        assert len(worker_calls) == (1 if fault == "duplicate-id" else 0)
        assert verdict["errors"] or load_ref(trial, verdict["behavior"])["errors"]

    def test_oracle_timeout_cancels_active_worker_and_waits_for_its_owned_result(self, tmp_path, monkeypatch):
        started, finished = threading.Event(), threading.Event()
        def waiting(value, roots, cancel_event):
            assert cancel_event is not None
            started.set()
            observed = cancel_event.wait(3)
            finished.set()
            assert observed, "Oracle termination must signal its active worker before returning"
            result = process_result(code=130)
            result["cancelled"] = True
            return result
        trial, _, _, verdict = observing_case(tmp_path, monkeypatch, callback=waiting, oracle_timeout=1)
        assert started.is_set() and finished.is_set()
        assert verdict["common_quality"] == "failed" and verdict["synthetic_validation"] != "passed"
        behavior = load_ref(trial, verdict["behavior"])
        assert len(behavior["observations"]) == 1 and (behavior["errors"] or verdict["errors"])

    def test_valid_worker_stdout_cannot_rescue_failed_owned_cleanup(self, tmp_path, monkeypatch):
        boundary = RoleBoundary()
        def uncertain(value, roots, cancel_event):
            boundary.cleanup = "failed"
            return process_result(stdout=encoded(response(value)))
        boundary.worker = uncertain
        trial, _, _, verdict = observing_case(tmp_path, monkeypatch, boundary=boundary, ignore_failure=True)
        assert verdict["common_quality"] == "failed" and verdict["synthetic_validation"] != "passed"
        behavior = load_ref(trial, verdict["behavior"])
        assert len(behavior["observations"]) == 1 and (behavior["errors"] or verdict["errors"])

    def test_worker_cannot_change_frozen_candidate_even_when_returning_valid_protocol(self, tmp_path, monkeypatch):
        def mutate(value, roots, cancel_event):
            (Path(roots["workspace"]) / "backend/app/service.py").write_text("changed by fictional worker\n")
            return process_result(stdout=encoded(response(value)))
        trial, _, _, verdict = observing_case(tmp_path, monkeypatch, callback=mutate, ignore_failure=True)
        assert verdict["common_quality"] == "failed"
        behavior = load_ref(trial, verdict["behavior"])
        errors = verdict["errors"] + behavior["errors"]
        assert any(error["code"] == "candidate-drift" for error in errors)
