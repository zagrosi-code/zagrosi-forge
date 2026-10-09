"""Offline preparation/finalization controls, never real native qualification.

The parent may execute only controller version reads and temporary Git. Native
setup/loading/writer execution is explicitly doubled. Independent assessment is
replaced by a finalization observer; these tests make no quality/acceptance claim.
Requires the separately admitted explicit loading.selected_entry addition.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import coding_trial_preparation as preparation
from coding_trial_delivery import read_delivery
from coding_trial_inventory import fingerprint, inventory
from coding_trial_native import read_native_runtime
from coding_trial_suite import _read_study, prepare_suite, run_suite
from native_admission_fixtures import attach_static_admission
from native_reservation_fixtures import creation_anchor
from test_trial_suite_native_loading import IMAGE_READBACK, NativeBoundary
from test_trial_suite_native_runtime import (
    HOST, REQUIREMENTS, SELECTED_ENTRY, byte_hash, forbid_credential_reads, make_native, save,
)
from trial_suite_fixtures import link
from trial_suite_prepare_cases import PYTHON, bytes_at, read_reference


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
    reason="Native reservation requires the admitted no-follow controller")

FIXED_ENV = {"HOME": "/home/forge", "CODEX_HOME": "/codex",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}
DELIVERY = "def normalize(value):\n    return value.removeprefix('v')\n"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bound(root, reference):
    return json.loads(read_reference(root, reference)[1])


@pytest.fixture(autouse=True)
def forbid_native_processes(monkeypatch):
    original = subprocess.Popen
    def checked(argv, *args, **kwargs):
        assert isinstance(argv, (tuple, list)) and argv, "No shell/native process is admitted"
        allowed = (Path(argv[0]).name == "git" or list(argv) == [str(PYTHON), "-I", "-B", "--version"])
        assert allowed, f"Offline preparation cannot launch this process: {argv!r}"
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", checked)


def inputs(tmp_path, *, plain=False, admitted=True):
    # Only the curator suite from this template is given to public preparation.
    # Its already prepared attempt is not reused, adopted or copied as runtime.
    seed = make_native(tmp_path / "template", plain=plain)
    arm = seed["suite"]["arms"][seed["arm"]]
    arm["loading"]["selected_entry"] = None if plain else SELECTED_ENTRY
    arm["workflow"] = "none" if plain else "unmeasured"  # Workflow/assessment have separate tests.
    if admitted:
        attach_static_admission(seed)
    else:
        save(seed["suite_root"] / "suite.json", seed["suite"])
    return seed


class SetupBoundary:
    """Actual preparation API, fictional process observations and install writes."""
    def __init__(self, seed, monkeypatch, *, fault=None):
        self.seed, self.fault, self.calls = seed, fault, []
        monkeypatch.setattr(preparation, "run_owned", self)

    def __call__(self, profile, layout, argv, *, cwd, env, prompt, timeout, output_limit, evidence_dir):
        directory = Path(evidence_dir)
        assert directory.name == f"{len(self.calls):03d}"
        assert directory.parent.name == "steps" and directory.parent.parent.name == "native-setup"
        assert directory.is_dir() and not any(directory.iterdir())
        setup = directory.parent.parent
        trial = setup.parent.parent
        expected_mounts = [
            {"source": str(setup / "codex"), "target": "/codex", "read_only": False},
            {"source": str(setup / "home"), "target": "/home/forge", "read_only": False},
            {"source": str(trial / "native-runtime/marketplace"), "target": "/marketplace", "read_only": True},
        ]
        assert layout == {"role": "preparation", "task": "normalize", "arm": self.seed["arm"],
                          "mounts": expected_mounts, "user": profile["user"], "network": "none"}
        assert str(cwd) == "/" and env == FIXED_ENV and prompt is None
        assert (timeout, output_limit) == (60, 1048576)
        assert profile == load(self.seed["suite_root"] / "profile.json")
        assert "exec" not in argv, "Offline installation cannot launch a model"
        operations = self.seed["preparation"]["executions"]
        number = len(self.calls)
        if number < len(operations):
            operation = operations[number]
            assert argv == operation["argv"]
            stdout = operation["stdout"]
            if argv[-3:] == ["plugin", "add", "--json"]:
                pytest.fail("The explicit plugin identity was lost")
            if "installedPath" in stdout:
                installed = json.loads(stdout)
                relative = installed["installedPath"].removeprefix("/codex/")
                cache = setup / "codex" / relative
                shutil.copytree(trial / "native-runtime/marketplace/payload", cache)
                if self.fault == "cache-bytes":
                    (cache / SELECTED_ENTRY).write_text("Unexpected transformed installation\n")
            # Installer state must not leak into the final home/codex roots.
            (setup / "home/installer.log").write_text("Fictional temporary home state\n")
            (setup / "codex/config.toml").write_text("# Fictional installer configuration\n")
        else:
            assert number == len(operations)
            assert argv == ["/usr/local/bin/python3", "-I", "-B", "-c", IMAGE_READBACK, HOST]
            stdout = json.dumps({"schema": "coding-trial-native-image/v1",
                "executable_sha256": self.seed["runtime"]["host_executable_sha256"],
                "requirements_sha256": hashlib.sha256(REQUIREMENTS.encode()).hexdigest(),
                "requirements_toml": REQUIREMENTS})
        process = {"returncode": 7 if self.fault == "nonzero" and number == 0 else 0,
                   "stdout": stdout, "stderr": "", "seconds": .125, "timed_out": False,
                   "stdout_bytes": len(stdout.encode()), "stderr_bytes": 0,
                   "stdout_truncated": self.fault == "truncated" and number == 0, "stderr_truncated": False}
        container = f"{number + 1:064x}"
        config = directory / "docker-config"
        config.mkdir()
        raw = directory / "docker-000.json"
        lifecycle = {"status": "unverified" if self.fault == "cleanup" and number == 0 else "verified",
                     "reason": "Explicit offline test double; no container exists", "evidence": [str(raw)]}
        result = {"process": process, "container_id": container, "lifecycle": lifecycle,
                  "identity": {"executable_sha256": "7" * 64, "docker_version": "fictional-docker",
                               "image_id": "sha256:" + "8" * 64}}
        save(raw, {"argv": [profile["docker_executable"], "--host", profile["endpoint"], "--config", str(config),
                             "start", "--attach", "--interactive", container],
                   "environment": {}, "process": deepcopy(process),
                   "started_at": "2026-10-09T10:00:00Z", "ended_at": "2026-10-09T10:00:00.125000Z"})
        if self.fault == "exception":
            self.calls.append({"argv": list(argv), "process": deepcopy(process), "result": None,
                               "evidence_dir": directory, "layout": deepcopy(layout),
                               "raw": {raw.name: raw.read_bytes()}})
            raise ValueError("unsupported-profile: Explicit test exception after one retained raw invocation")
        (directory / "container.cid").write_text(container + "\n")
        save(directory / "lifecycle.json", {"container_id": container, "owner": f"{number + 1:032x}", **lifecycle})
        save(directory / "owned-result.json", result)
        self.calls.append({"argv": list(argv), "process": deepcopy(process), "result": deepcopy(result),
                           "evidence_dir": directory, "layout": deepcopy(layout),
                           "raw": {name: (directory / name).read_bytes()
                                   for name in (raw.name, "container.cid", "lifecycle.json")}})
        return result


def assert_setup_evidence(trial, attempt, setup, seed, *, status):
    reference = attempt["native"]["setup"]
    assert reference["path"] == "private/native-setup/setup.json"
    index = bound(trial, reference)
    assert set(index) == {"schema", "identity", "profile_sha256", "layout", "environment",
                          "bounds", "status", "steps", "error"}
    assert index["schema"] == "coding-trial-native-setup/v1"
    assert index["identity"] == attempt["identity"]
    assert index["profile_sha256"] == byte_hash(seed["suite_root"] / "profile.json")
    assert index["layout"] == setup.calls[0]["layout"]
    assert index["environment"] == FIXED_ENV
    assert index["bounds"] == {"timeout_seconds": 60, "output_bytes": 1048576}
    assert index["status"] == status
    assert (index["error"] is None) is (status == "passed")
    order = (["version", "discovery", "image"] if seed["suite"]["arms"][seed["arm"]]["product"] is None
             else ["version", "marketplace-add", "plugin-add", "discovery", "image"])
    assert len(index["steps"]) == len(setup.calls)
    for number, (step, call) in enumerate(zip(index["steps"], setup.calls)):
        assert set(step) == {"number", "id", "argv", "cwd", "prompt", "started_at", "ended_at",
                             "result", "lifecycle", "evidence", "error"}
        assert (step["number"], step["id"], step["argv"], step["cwd"], step["prompt"]) == (
            number, order[number], call["argv"], "/", None)
        started = datetime.fromisoformat(step["started_at"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(step["ended_at"].replace("Z", "+00:00"))
        assert started.utcoffset().total_seconds() == ended.utcoffset().total_seconds() == 0
        assert started <= ended
        relative = call["evidence_dir"].relative_to(trial).as_posix()
        evidence_names = [name for name in call["raw"] if name != "lifecycle.json"]
        assert [item["path"] for item in step["evidence"]] == [relative + "/" + name for name in evidence_names]
        for item in step["evidence"]:
            assert read_reference(trial, item)[1] == call["raw"][Path(item["path"]).name]
        if call["result"] is None:
            assert step["result"] is None and step["lifecycle"] is None
        else:
            assert step["result"]["path"] == relative + "/result.json"
            assert bound(trial, step["result"]) == call["result"]
            assert step["lifecycle"]["path"] == relative + "/lifecycle.json"
            assert read_reference(trial, step["lifecycle"])[1] == call["raw"]["lifecycle.json"]
            assert load(call["evidence_dir"] / "owned-result.json") == call["result"]
            assert call["result"]["lifecycle"]["evidence"] == [
                str(call["evidence_dir"] / "docker-000.json")]
        if status == "passed":
            assert step["error"] is None
        if step["error"] is not None:
            assert set(step["error"]) == {"code", "message", "path"}
            assert step["error"]["code"] and step["error"]["message"]
    if status == "failed":
        assert set(index["error"]) == {"code", "message", "path"}
        assert index["error"]["code"] and index["error"]["message"]
        assert index["steps"][-1]["error"] is not None
    return index


class PreparedNativeBoundary(NativeBoundary):
    """Reuse independent loading controls at the actual newly reserved roots."""
    def __init__(self, seed, monkeypatch, *, fault=None, writer_code=0, writer_cleanup="verified"):
        super().__init__(seed, monkeypatch, fault=fault)
        self.writer_code = writer_code
        self.writer_cleanup = writer_cleanup
        self.original_anchors = None

    def isolation(self, suite, suite_root, layout, evidence_dir, *, roots, auth_file=None):
        assert auth_file is None
        native = Path(roots["native_runtime"])
        trial, control = native.parent, native.parent / "private"
        self.fixture = {"suite": suite, "suite_root": Path(suite_root), "arm": layout["arm"],
            "roots": deepcopy(roots), "native": native, "runtime": load(native / "runtime.json"),
            "preparation": load(control / "native-preparation.json"),
            "reservation": load(control / "reservation.json"),
            "loading_evidence": control / "loading", "writer_evidence": control / "writer"}
        anchors = self.fixture["reservation"]["anchors"]
        if self.original_anchors is None:
            self.original_anchors = deepcopy(anchors)
        assert anchors == self.original_anchors
        for anchor in anchors.values():
            assert creation_anchor(Path(anchor["path"])) == anchor
        if Path(evidence_dir).is_relative_to(control / "writer"):
            assert not (trial / "workspace/smoke-change.txt").exists()
            assert not list((trial / "workspace").glob("forge-loading-*"))
            assert (trial / "workspace/.git").is_dir(), "Bootstrap must restore initial Git in place"
        return super().isolation(suite, suite_root, layout, evidence_dir, roots=roots, auth_file=auth_file)

    def owned(self, profile, layout, argv, **kwargs):
        result = super().owned(profile, layout, argv, **kwargs)
        if self.calls[-1]["stage"] == "writer":
            workspace = Path(self.fixture["roots"]["workspace"])
            (workspace / "writer-result.txt").unlink()
            (workspace / "backend/app/service.py").write_text(DELIVERY)
            result["process"]["returncode"] = self.writer_code
            result["lifecycle"]["status"] = self.writer_cleanup
            folder = Path(kwargs["evidence_dir"])
            raw = load(folder / "docker-000.json")
            raw["process"] = deepcopy(result["process"])
            save(folder / "docker-000.json", raw)
            lifecycle = load(folder / "lifecycle.json")
            lifecycle["status"] = self.writer_cleanup
            save(folder / "lifecycle.json", lifecycle)
            save(folder / "owned-result.json", result)
            self.calls[-1]["result"] = deepcopy(result)
        return result


def finalization_observer(monkeypatch):
    calls = []
    def observe(trial, *, review=None):
        assert review is None
        attempt = load(trial / "trial.json")
        assert attempt["status"] == "completed" and attempt["runner"] and attempt["candidate"]
        _, suite, root = _read_study(attempt["study"])
        read_delivery(trial, attempt, suite, root)
        bound(trial, attempt["runner"])
        calls.append(deepcopy(attempt))
        return {"test_only": "Finalization observed; no assessment/acceptance asserted"}
    monkeypatch.setattr("coding_trial_assessment.assess_suite", observe)
    return calls


class TestNativePreparation:
    @pytest.mark.parametrize("alias", ["private-oracle", "symlink", "hardlink"])
    def test_auth_alias_is_rejected_before_freezing_or_reading_private_material(self, tmp_path, monkeypatch, alias):
        seed = inputs(tmp_path)
        seed["suite"]["host"]["credentials"] = "codex-native-auth"
        save(seed["suite_root"] / "suite.json", seed["suite"])
        setup = SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch)
        if alias == "private-oracle":
            protected = supplied = seed["suite_root"] / "checks/oracle.py"
        else:
            protected = tmp_path / "external-test-auth.json"
            protected.write_text("Synthetic private fixture only; never real credentials.\n")
            supplied = tmp_path / "alias-test-auth.json"
            if alias == "symlink":
                link(supplied, str(protected))
            else:
                try:
                    os.link(protected, supplied)
                except (OSError, NotImplementedError):
                    pytest.skip("Hard links are unavailable")
        forbid_credential_reads(monkeypatch, protected)
        trial = tmp_path / "fresh"
        with pytest.raises(ValueError):
            prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                          assessor_python=PYTHON, auth_file=supplied)
        assert not trial.exists() and not trial.with_name(trial.name + ".study").exists()
        assert setup.calls == [] and native.calls == [] and native.isolations == []

    def test_external_auth_uses_only_metadata_and_empty_final_mountpoint(self, tmp_path, monkeypatch):
        seed = inputs(tmp_path)
        seed["suite"]["host"]["credentials"] = "codex-native-auth"
        save(seed["suite_root"] / "suite.json", seed["suite"])
        auth = tmp_path / "external-test-auth.json"
        marker = b"Synthetic private fixture marker; no provider credentials.\n"
        auth.write_bytes(marker)
        original = auth.stat()
        forbid_credential_reads(monkeypatch, auth)
        setup = SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch)
        trial = tmp_path / "fresh"
        attempt = prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                                assessor_python=PYTHON, auth_file=auth)
        assert attempt["status"] == "prepared" and setup.calls
        assert native.calls == [] and native.isolations == []
        _, suite, root = _read_study(attempt["study"])
        assert read_native_runtime(suite, root, "normalize", seed["arm"], attempt["roots"], auth_file=auth) == bound(
            trial, attempt["native"]["runtime"])
        placeholder = trial / "native-runtime/codex/auth.json"
        assert not placeholder.is_symlink() and placeholder.read_bytes() == b""
        reservation = load(trial / "private/reservation.json")
        assert reservation["auth"] == {"path": str(auth), "device": original.st_dev,
                                       "inode": original.st_ino, "mode": original.st_mode}
        assert all(marker not in raw for raw in bytes_at(trial).values())
        after = auth.stat()
        assert (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == (
            original.st_dev, original.st_ino, original.st_mode, original.st_size, original.st_mtime_ns)

    def test_product_preparation_cannot_infer_an_undeclared_installed_entry(self, tmp_path, monkeypatch):
        seed = inputs(tmp_path)
        del seed["suite"]["arms"][seed["arm"]]["loading"]["selected_entry"]
        save(seed["suite_root"] / "suite.json", seed["suite"])
        setup = SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch)
        with pytest.raises(ValueError):
            prepare_suite(tmp_path / "fresh", seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                          assessor_python=PYTHON)
        assert setup.calls == [] and native.calls == [] and native.isolations == []

    @pytest.mark.parametrize("plain,admitted", [(False, True), (True, True), (False, False)])
    def test_prepare_records_only_actual_offline_observations_and_creation_reservation(self, tmp_path, monkeypatch,
                                                                                       plain, admitted):
        seed = inputs(tmp_path, plain=plain, admitted=admitted)
        setup = SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch)
        trial = tmp_path / "fresh"
        result = prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"], assessor_python=PYTHON)
        assert result == load(trial / "trial.json") and result["status"] == "prepared"
        assert result["runner"] is None and result["candidate"] is None and result["assessments"] == []
        assert native.calls == [] and native.isolations == []
        assert not (trial / "private/loading").exists() and not (trial / "private/writer").exists()
        assert len(setup.calls) == len(seed["preparation"]["executions"]) + 1
        assert_setup_evidence(trial, result, setup, seed, status="passed")
        saved = bound(trial, result["native"]["preparation"])
        assert saved == seed["preparation"]
        assert saved["executions"] == [{"argv": call["argv"], **{key: call["process"][key]
                                        for key in ("returncode", "stdout", "stderr")}} for call in setup.calls[:-1]]
        assert all(load(call["evidence_dir"] / "owned-result.json") == call["result"] for call in setup.calls)
        _, suite, root = _read_study(result["study"])
        runtime = read_native_runtime(suite, root, "normalize", seed["arm"], result["roots"])
        assert runtime == bound(trial, result["native"]["runtime"])
        assert runtime["selected_entry"] == (None if plain else SELECTED_ENTRY)
        assert runtime["host_executable_sha256"] == seed["runtime"]["host_executable_sha256"]
        assert runtime["launch"] == seed["runtime"]["launch"]
        assert inventory(trial / "native-runtime/home") == {}
        assert set(inventory(trial / "native-runtime/codex")) == {"plugins"}
        reservation = load(trial / "private/reservation.json")
        initial = (trial / "private/initial-attempt.json").read_bytes()
        assert initial == (trial / "trial.json").read_bytes()
        assert reservation["attempt_record"]["sha256"] == hashlib.sha256(initial).hexdigest()
        assert reservation["runtime_sha256"] == byte_hash(trial / "native-runtime/runtime.json")
        assert reservation["snapshots"]["workspace_git"] is not None
        for anchor in reservation["anchors"].values():
            assert creation_anchor(Path(anchor["path"])) == anchor
        assert all(result["native"][key] is None for key in
                   ("reservation", "loading_source", "loading_derived", "execution_manifest", "restoration"))
        assert result["admission_gates"]["loading"]["status"] == "missing"
        assert result["admission_gates"]["isolation"]["status"] == "missing"
        assert result["admission_gates"]["environment"]["status"] == ("passed" if admitted else "missing")

    @pytest.mark.parametrize("fault", ["nonzero", "truncated", "cleanup", "cache-bytes"])
    def test_bad_setup_retains_raw_failure_without_runtime_reservation_or_model(self, tmp_path, monkeypatch, fault):
        seed = inputs(tmp_path)
        setup = SetupBoundary(seed, monkeypatch, fault=fault)
        native = PreparedNativeBoundary(seed, monkeypatch)
        trial = tmp_path / "fresh"
        with pytest.raises(ValueError):
            prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"], assessor_python=PYTHON)
        attempt = load(trial / "trial.json")
        assert attempt["status"] == "failed" and attempt["error"]
        assert attempt["runner"] is None and attempt["candidate"] is None
        assert not (trial / "private/reservation.json").exists()
        assert attempt["native"]["runtime"] is None
        assert setup.calls and native.calls == [] and native.isolations == []
        assert all(load(call["evidence_dir"] / "owned-result.json") == call["result"] for call in setup.calls)
        assert_setup_evidence(trial, attempt, setup, seed, status="failed")

    def test_setup_exception_binds_partial_raw_evidence_without_inventing_a_result(self, tmp_path, monkeypatch):
        seed = inputs(tmp_path)
        setup = SetupBoundary(seed, monkeypatch, fault="exception")
        native = PreparedNativeBoundary(seed, monkeypatch)
        trial = tmp_path / "fresh"
        with pytest.raises(ValueError):
            prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"], assessor_python=PYTHON)
        attempt = load(trial / "trial.json")
        assert attempt["status"] == "failed" and attempt["error"]
        assert attempt["runner"] is None and attempt["candidate"] is None
        assert attempt["native"]["runtime"] is None and attempt["native"]["preparation"] is None
        assert len(setup.calls) == 1 and native.calls == [] and native.isolations == []
        index = assert_setup_evidence(trial, attempt, setup, seed, status="failed")
        step = index["steps"][0]
        assert "Explicit test exception" in step["error"]["message"]
        assert step["result"] is None and step["lifecycle"] is None
        assert not (setup.calls[0]["evidence_dir"] / "result.json").exists()
        assert not (setup.calls[0]["evidence_dir"] / "lifecycle.json").exists()
        assert not (trial / "private/reservation.json").exists()

    def test_prepared_native_attempt_is_never_adopted_as_a_run(self, tmp_path, monkeypatch):
        seed = inputs(tmp_path)
        setup = SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch)
        trial = tmp_path / "fresh"
        prepare_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"], assessor_python=PYTHON)
        before, calls = bytes_at(trial), len(setup.calls)
        with pytest.raises(ValueError, match="^input-exists:"):
            run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                      assessor_python=PYTHON, qualify_loading=True)
        assert bytes_at(trial) == before and len(setup.calls) == calls
        assert native.calls == [] and native.isolations == []

    def test_missing_loading_does_not_implicitly_smoke_or_launch_writer(self, tmp_path, monkeypatch):
        seed = inputs(tmp_path)
        setup = SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch)
        assessments = finalization_observer(monkeypatch)
        trial = tmp_path / "fresh"
        with pytest.raises(ValueError, match="loading-unqualified"):
            run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"], assessor_python=PYTHON)
        record = load(trial / "trial.json")
        assert record["status"] == "failed" and record["runner"] is None
        assert setup.calls and native.calls == [] and native.isolations == [] and assessments == []
        assert not (trial / "private/loading").exists() and not (trial / "private/writer").exists()

    @pytest.mark.parametrize("plain,writer_code", [(False, 0), (True, 0), (False, 9)])
    def test_explicit_loading_packages_only_selected_proof_then_finalizes_original_identity(self, tmp_path, monkeypatch,
                                                                                         plain, writer_code):
        seed = inputs(tmp_path, plain=plain)
        SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch, writer_code=writer_code)
        assessments = finalization_observer(monkeypatch)
        source_raw = (seed["suite_root"] / "suite.json").read_bytes()
        trial = tmp_path / "fresh"
        result = run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                           assessor_python=PYTHON, qualify_loading=True)
        assert result == load(trial / "trial.json") and result["status"] == "completed"
        assert len(assessments) == 1
        assert [call["stage"] for call in native.calls] == ["image", "version", "discovery", "smoke", "image", "version", "writer"]
        assert len(native.isolations) == 3
        study, original, root = _read_study(result["study"])
        assert (root / study["manifest"]["path"]).read_bytes() == source_raw
        assert (seed["suite_root"] / "suite.json").read_bytes() == source_raw
        refs = result["native"]
        source = bound(trial, refs["loading_source"])
        derived = bound(root, refs["loading_derived"])
        manifest_ref = refs["execution_manifest"]
        assert set(manifest_ref) == {"path", "sha256", "suite_sha256"}
        execution = bound(root, {key: manifest_ref[key] for key in ("path", "sha256")})
        expected = deepcopy(original)
        expected["arms"][seed["arm"]]["loading"]["receipt"] = refs["loading_derived"]["path"]
        assert execution == expected
        assert manifest_ref["suite_sha256"] == fingerprint(execution)
        assert result["identity"]["suite_sha256"] == study["manifest"]["suite_sha256"] == fingerprint(original)
        assert manifest_ref["suite_sha256"] != result["identity"]["suite_sha256"]
        prefix = "_attempts/0/loading/"
        expected_derived = deepcopy(source)
        for item in expected_derived["evidence"]:
            item["path"] = prefix + item["path"]
        for check in expected_derived["checks"]:
            check["evidence"] = [prefix + name for name in check["evidence"]]
        assert derived == expected_derived
        source_dir = (trial / refs["loading_source"]["path"]).parent
        for item in source["evidence"]:
            assert (root / prefix / item["path"]).read_bytes() == (source_dir / item["path"]).read_bytes()
            assert byte_hash(root / prefix / item["path"]) == item["sha256"]
        restoration = bound(trial, refs["restoration"])
        assert restoration["status"] == "passed"
        reservation = bound(trial, refs["reservation"])
        assert reservation["anchors"] == native.original_anchors
        initial = bound(trial, {"path": "private/initial-attempt.json", "sha256": reservation["attempt_record"]["sha256"]})
        assert initial["status"] == "running" and initial["native"]["reservation"] is None
        runner = bound(trial, result["runner"])
        assert runner["suite_sha256"] == manifest_ref["suite_sha256"]
        assert runner["process"]["returncode"] == writer_code
        assert (trial / result["candidate"]["path"] / "backend/app/service.py").read_text() == DELIVERY
        assert not (trial / "workspace/smoke-change.txt").exists()
        assert not (trial / "private/execution.lock").exists()

    @pytest.mark.parametrize("fault,restoration", [("missing-output", "passed"), ("cleanup", "not_attempted")])
    def test_failed_loading_or_uncertain_cleanup_never_continues_to_writer(self, tmp_path, monkeypatch, fault, restoration):
        seed = inputs(tmp_path)
        SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch, fault=fault)
        assessments = finalization_observer(monkeypatch)
        trial = tmp_path / "fresh"
        with pytest.raises(ValueError):
            run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                      assessor_python=PYTHON, qualify_loading=True)
        attempt = load(trial / "trial.json")
        assert attempt["status"] == "failed" and attempt["runner"] is None
        assert "writer" not in [call["stage"] for call in native.calls] and assessments == []
        assert not (trial / "private/writer").exists()
        assert load(trial / "private/loading/restoration.json")["status"] == restoration
        assert (trial / "workspace/smoke-change.txt").exists() is (fault == "cleanup")
        assert attempt["native"]["loading_derived"] is None and attempt["native"]["execution_manifest"] is None

    def test_unverified_writer_cleanup_retains_failed_lifecycle_and_only_diagnostic_delivery(self, tmp_path, monkeypatch):
        seed = inputs(tmp_path)
        SetupBoundary(seed, monkeypatch)
        native = PreparedNativeBoundary(seed, monkeypatch, writer_cleanup="unverified")
        observed = finalization_observer(monkeypatch)
        trial = tmp_path / "fresh"
        try:
            run_suite(trial, seed["suite_root"] / "suite.json", "normalize", seed["arm"],
                      assessor_python=PYTHON, qualify_loading=True)
        except ValueError:
            # Infrastructure failure may raise after preserving its truthful
            # terminal record; diagnostic output need not be discarded.
            pass
        attempt = load(trial / "trial.json")
        assert attempt["status"] in {"completed", "failed"} and attempt["runner"] is not None
        assert native.calls[-1]["stage"] == "writer"
        assert native.calls[-1]["result"]["lifecycle"]["status"] == "unverified"
        runner = bound(trial, attempt["runner"])
        assert runner["process"]["returncode"] == 0
        assert runner["isolation"]["lifecycle"]["status"] == "unverified"
        assert runner["isolation"]["status"] == "failed"
        assert (trial / "workspace/backend/app/service.py").read_text() == DELIVERY
        if attempt["candidate"] is not None:
            assert (trial / attempt["candidate"]["path"] / "backend/app/service.py").read_text() == DELIVERY
        # Assessment semantics are independently tested. Any finalization
        # observer must receive the actual failed lifecycle, never a success.
        assert all(bound(trial, record["runner"])["isolation"]["status"] == "failed" for record in observed)
