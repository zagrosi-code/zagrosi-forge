"""Synthetic orchestration controls: no Docker, native CLI, provider or credentials.

Passed fields below are explicit test-double responses, never live qualification
evidence. Public runner functions and all input/record validation stay real.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_inventory import fingerprint, inventory
from coding_trial_runner import qualify_native_loading, run_suite_writer
from native_reservation_fixtures import make_reserved_native
from test_trial_suite_isolation import tree_state
from test_trial_suite_native_events import command_event, collab_event, READER
from test_trial_suite_native_runtime import byte_hash, save, REQUIREMENTS
from trial_suite_fixtures import digest


pytestmark = pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
    reason="Reservation restoration requires the admitted no-follow controller platform")

IMAGE_READBACK = """import hashlib,json,sys
from pathlib import Path
policy=Path('/etc/codex/requirements.toml').read_bytes()
print(json.dumps({'schema':'coding-trial-native-image/v1',
 'executable_sha256':hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
 'requirements_sha256':hashlib.sha256(policy).hexdigest(),
 'requirements_toml':policy.decode('utf-8')},sort_keys=True))
"""
ACTION_SCRIPT = """import json,sys
from pathlib import Path
source,target,private=map(Path,sys.argv[1:4]); nonce=sys.argv[4]
def denied(operation):
    try: operation()
    except (FileNotFoundError,PermissionError): return True
    return False
def private_write():
    with private.open('x') as stream: stream.write(nonce)
value={'nonce':nonce,'task_read':source.read_text()==nonce,
 'private_read_denied':denied(private.read_bytes),'private_write_denied':denied(private_write)}
with target.open('x') as stream: json.dump(value,stream,sort_keys=True)
print(json.dumps(value,sort_keys=True))
"""
ADAPTER_SOURCES = tuple("tools/" + name for name in (
    "coding_trial_isolation.py", "coding_trial_docker.py", "coding_trial_process.py", "coding_trial_inventory.py",
    "coding_trial_qualification.py", "coding_trial_manifest.py", "coding_trial_native.py",
    "coding_trial_loading.py", "coding_trial_loading_evidence.py", "coding_trial_reservation.py")) + ("scripts",)
PROBE_SOURCES = ("tools/coding_trial_isolation.py", "tools/coding_trial_docker.py")
LOADING_CHECKS = {"native-discovery", "entry-routing", "required-tools", "task-read-write",
                  "private-access-denied", "termination"}


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Synthetic orchestration tests must never start a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def fixture_at(tmp_path, *, plain=False, subagents=False):
    return make_reserved_native(tmp_path, initial_attempt_bytes=b'{"fixture":"opaque initial bytes"}\n',
                                plain=plain, subagents=subagents, with_admission=True)


def qualify(fixture):
    return qualify_native_loading(fixture["suite"], fixture["suite_root"], "normalize", fixture["arm"],
                                  fixture["roots"], fixture["loading_evidence"])


def mutable_state(fixture):
    return {name: tree_state(Path(fixture["reservation"]["anchors"][name]["path"]))
            for name in ("workspace", "home", "codex", "generated")}


def mutable_ids(fixture):
    return {name: (Path(anchor["path"]).stat().st_dev, Path(anchor["path"]).stat().st_ino)
            for name, anchor in fixture["reservation"]["anchors"].items()}


def package_loading(fixture, receipt):
    """Exercise the documented derived-copy convention, preserving raw bytes."""
    prefix = "_attempts/0/loading"
    bundle = fixture["suite_root"] / prefix
    shutil.copytree(fixture["loading_evidence"], bundle)
    derived = deepcopy(receipt)
    for item in derived["evidence"]:
        item["path"] = prefix + "/" + item["path"]
    for check in derived["checks"]:
        check["evidence"] = [prefix + "/" + path for path in check["evidence"]]
    save(bundle / "receipt.json", derived)
    suite = deepcopy(fixture["suite"])
    suite["arms"][fixture["arm"]]["loading"]["receipt"] = prefix + "/receipt.json"
    save(bundle.parent / "execution.json", suite)
    return suite, bundle


class NativeBoundary:
    """Only the two documented actual execution seams are replaced."""
    def __init__(self, fixture, monkeypatch, *, fault=None):
        self.fixture, self.fault = fixture, fault
        self.calls, self.isolations = [], []
        monkeypatch.setattr("coding_trial_loading.qualify_profile", self.isolation)
        monkeypatch.setattr("coding_trial_loading.run_owned", self.owned)

    def isolation(self, suite, suite_root, layout, evidence_dir, *, roots, auth_file=None):
        evidence_dir = Path(evidence_dir)
        evidence_dir.mkdir(parents=True)
        self.isolations.append(evidence_dir.name)
        profile = json.loads((suite_root / suite["host"]["isolation"]["profile"]).read_text(encoding="utf-8"))
        save(evidence_dir / "explicit-test-double.json", {"fictional": True, "invocations": []})
        # Fictional qualifier timing remains separate from the writer process duration.
        save(evidence_dir / "timing.json", {"started_at": "2026-10-09T10:00:00Z",
             "ended_at": "2026-10-09T10:00:00.500000Z", "seconds": 0.5})
        checks = ["task-read-write", "product-read-only", "private-read-denied", "private-write-denied",
                  "relative-symlink-denied", "child-private-denied", "detached-child-stopped",
                  "owned-container-removed", "network-denied"]
        receipt = {"schema": "coding-trial-qualification/v1", "kind": "isolation",
                   "bindings": {"docker_executable_sha256": "7" * 64, "docker_version": "fictional-docker",
                       **{key: profile[key] for key in ("context", "endpoint", "server_id", "server_version",
                                                        "image_digest", "platform", "network")},
                       "adapter_sha256": fingerprint(inventory(ROOT, included=ADAPTER_SOURCES)),
                       "probe_sha256": fingerprint(inventory(ROOT, included=PROBE_SOURCES)),
                       "profile_sha256": byte_hash(suite_root / suite["host"]["isolation"]["profile"]),
                       "layout_sha256": digest({key: layout[key] for key in ("mounts", "user", "network")}),
                       "limits_sha256": digest(profile["limits"])},
                   "checks": [{"id": key, "status": "failed" if self.fault == "final-isolation" and
                                evidence_dir.name == "isolation-after" else "passed",
                                "evidence": ["explicit-test-double.json"]} for key in checks],
                   "producer": {"name": "explicit-fictional-test-double", "independent": False, "purpose": "actual"},
                   "execution": {"argv": ["/never-run/fixture-isolation"], "executable_sha256": "7" * 64,
                                 "version": "fictional-docker", "platform": "fictional-controller",
                                 "started_at": "2026-10-09T10:00:00Z", "ended_at": "2026-10-09T10:00:01Z",
                                 "returncode": 0},
                   "evidence": [{"path": name, "sha256": byte_hash(evidence_dir / name)}
                                for name in ("explicit-test-double.json", "timing.json")]}
        save(evidence_dir / "qualification.json", receipt)
        return receipt

    def owned(self, profile, layout, argv, *, cwd, env, prompt, timeout, output_limit, evidence_dir):
        f, runtime = self.fixture, self.fixture["runtime"]
        evidence_dir = Path(evidence_dir)
        writer = f["writer_evidence"] in evidence_dir.parents
        stage = "writer" if writer and argv == runtime["launch"]["argv"] else evidence_dir.name
        assert stage in {"image", "version", "discovery", "smoke", "writer"}
        assert str(cwd) == "/workspace" and env == runtime["launch"]["environment"]
        assert (timeout, output_limit) == {"image": (15, 65536), "version": (15, 65536),
            "discovery": (30, 1048576), "smoke": (900, 8388608), "writer": (900, 8388608)}[stage]
        assert evidence_dir.is_dir() and not any(evidence_dir.iterdir())
        process = {"returncode": 0, "stdout": "", "stderr": "", "seconds": 0.125,
                   "timed_out": False, "stdout_bytes": 0, "stderr_bytes": 0,
                   "stdout_truncated": False, "stderr_truncated": False}
        if stage == "image":
            assert argv == ["/usr/local/bin/python3", "-I", "-B", "-c", IMAGE_READBACK, runtime["host_executable"]]
            output = {"schema": "coding-trial-native-image/v1", "executable_sha256": runtime["host_executable_sha256"],
                      "requirements_sha256": runtime["requirements_sha256"], "requirements_toml": REQUIREMENTS}
            if self.fault == "image-identity":
                output["executable_sha256"] = "0" * 64
            process["stdout"] = json.dumps(output)
        elif stage == "version":
            assert argv == [runtime["host_executable"], "--version"]
            process["stdout"] = runtime["host_version"] + "\n"
            if self.fault == "version":
                process["stdout"] = "codex-cli wrong-version\n"
            elif self.fault == "immutable-input":
                (Path(f["roots"]["product"]) / runtime["selected_entry"]).write_text("Changed frozen product", encoding="utf-8")
        elif stage == "discovery":
            operation = f["preparation"]["executions"][-1]
            assert argv == operation["argv"]
            process["stdout"] = operation["stdout"]
            # Real discovery is allowed to change disposable native state.
            (f["native"] / "home/discovery.log").write_text("Synthetic discovery state", encoding="utf-8")
            (f["native"] / "codex/discovery.log").write_text("Synthetic discovery state", encoding="utf-8")
        else:
            assert argv == runtime["launch"]["argv"]
            if stage == "writer":
                (Path(f["roots"]["workspace"]) / "writer-result.txt").write_text("Synthetic writer change", encoding="utf-8")
                process["stdout"] = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
            else:
                self.smoke(process, prompt)
        container_id = f"{len(self.calls) + 1:064x}"
        owner = f"{len(self.calls) + 1:032x}"
        command_path = evidence_dir / "docker-000.json"
        lifecycle = {"status": "unverified" if stage == "smoke" and self.fault == "cleanup" else "verified",
                     "reason": "Explicit synthetic lifecycle test response",
                     "evidence": [str(command_path)]}
        process["stdout_bytes"] = len(process["stdout"].encode("utf-8"))
        process["stderr_bytes"] = len(process["stderr"].encode("utf-8"))
        result = {"process": process, "container_id": container_id, "lifecycle": lifecycle,
                  "identity": {"executable_sha256": "7" * 64, "docker_version": "fictional-docker",
                               "image_id": "sha256:" + "8" * 64}}
        # Fictional observation at the mocked executor boundary, never real
        # lifecycle evidence. The retained and returned process are identical.
        config = evidence_dir / "docker-config"
        config.mkdir()
        save(command_path, {"argv": [profile["docker_executable"], "--host", profile["endpoint"],
             "--config", str(config), "start", "--attach", "--interactive", container_id],
             "environment": {}, "process": deepcopy(process), "started_at": "2026-10-09T10:00:00Z",
             "ended_at": "2026-10-09T10:00:00.125000Z"})
        save(evidence_dir / "lifecycle.json", {"container_id": container_id, "owner": owner, **lifecycle})
        save(evidence_dir / "owned-result.json", result)
        self.calls.append({"stage": stage, "argv": list(argv), "environment": deepcopy(env), "prompt": prompt,
                           "result": deepcopy(result)})
        return result

    def smoke(self, process, prompt):
        f, runtime = self.fixture, self.fixture["runtime"]
        workspace = Path(f["roots"]["workspace"])
        controls = list(workspace.glob("forge-loading-*"))
        assert len(controls) == 1
        control = controls[0]
        nonce = control.name.removeprefix("forge-loading-")
        assert len(nonce) == 32 and all(char in "0123456789abcdef" for char in nonce)
        assert (control / "input.txt").read_text(encoding="utf-8") == nonce
        private = f["loading_evidence"] / "private-control.txt"
        assert private.read_text(encoding="utf-8") == nonce
        action = shlex.join(["/usr/local/bin/python3", "-I", "-B", "-c", ACTION_SCRIPT,
                            "/workspace/" + control.name + "/input.txt", "/workspace/" + control.name + "/output.json",
                            str(private), nonce])
        assert action in prompt
        output = {"nonce": nonce, "task_read": True, "private_read_denied": True, "private_write_denied": True}
        if self.fault != "missing-output":
            file_output = output | ({"nonce": "wrong"} if self.fault == "output-nonce" else {})
            (control / "output.json").write_text(json.dumps(file_output, sort_keys=True), encoding="utf-8")
        events = []
        if runtime["installed"] is not None:
            entry = shlex.join(["/usr/local/bin/python3", "-I", "-B", "-c", READER,
                                runtime["installed"]["path"] + "/" + runtime["selected_entry"]])
            assert entry in prompt
            entry_bytes = (Path(f["roots"]["product"]) / runtime["selected_entry"]).read_bytes()
            events.append(command_event("entry", entry, entry_bytes.decode("utf-8")))
        events.append(command_event("action", action, json.dumps(output, sort_keys=True) + "\n"))
        if f["suite"]["host"]["capabilities"]["subagents"]:
            events += [collab_event("spawn", "spawn_agent"), collab_event("wait", "wait", child_status="completed")]
        process["stdout"] = "\n".join(json.dumps(event) for event in events) + "\n"
        if self.fault == "nonzero":
            process["returncode"] = 7
        elif self.fault == "truncated":
            process["stdout_truncated"] = True
        elif self.fault == "private-change":
            private.write_text("Changed synthetic private control", encoding="utf-8")
        (workspace / "smoke-change.txt").write_text("Synthetic disposable smoke edit", encoding="utf-8")


@pytest.mark.parametrize("plain,subagents", [(False, False), (True, False), (False, True)])
def test_loading_observes_exact_commands_and_restores_same_owned_roots(tmp_path, monkeypatch, plain, subagents):
    f = fixture_at(tmp_path, plain=plain, subagents=subagents)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = NativeBoundary(f, monkeypatch)
    receipt = qualify(f)
    assert {row["id"] for row in receipt["checks"]} == LOADING_CHECKS | ({"subagent-execution"} if subagents else set())
    assert all(row["status"] == "passed" for row in receipt["checks"])
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery", "smoke"]
    assert boundary.isolations == ["isolation-before", "isolation-after"]
    assert receipt["execution"]["argv"] == boundary.calls[-1]["argv"]
    assert receipt["execution"]["returncode"] == boundary.calls[-1]["result"]["process"]["returncode"]
    assert receipt["execution"]["executable_sha256"] == f["runtime"]["host_executable_sha256"]
    assert receipt["execution"]["version"] == f["runtime"]["host_version"]
    assert receipt["execution"]["platform"] == "linux/amd64"
    assert mutable_state(f) == before and mutable_ids(f) == identities
    assert not f["writer_evidence"].exists()
    assert not (f["native"].parent / "private/execution.lock").exists()
    execution = json.loads((f["loading_evidence"] / "loading-execution.json").read_text(encoding="utf-8"))
    for key in ("started_at", "ended_at"):
        assert receipt["execution"][key] == execution["invocations"][-1][key]
    for recorded, actual in zip(execution["invocations"], boundary.calls, strict=True):
        assert recorded["id"] == actual["stage"] and recorded["argv"] == actual["argv"]
        assert recorded["process"] == actual["result"]["process"]
        assert all(not Path(path).is_absolute() for path in recorded["lifecycle"]["evidence"])
    assert f["trial_path"].read_bytes() == f["initial_attempt_bytes"]


@pytest.mark.parametrize("fault", ["missing-output", "output-nonce", "private-change", "nonzero", "truncated", "final-isolation"])
def test_failed_smoke_or_readback_cannot_qualify_a_writer(tmp_path, monkeypatch, fault):
    f = fixture_at(tmp_path)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = NativeBoundary(f, monkeypatch, fault=fault)
    receipt = qualify(f)
    assert any(row["status"] == "failed" for row in receipt["checks"])
    assert boundary.calls[-1]["stage"] == "smoke"
    assert receipt["execution"]["returncode"] == (7 if fault == "nonzero" else 0)
    assert mutable_state(f) == before and mutable_ids(f) == identities
    derived, _ = package_loading(f, receipt)
    calls_before = len(boundary.calls), len(boundary.isolations)
    with pytest.raises(ValueError):
        run_suite_writer(derived, f["suite_root"], "normalize", f["arm"], f["roots"], f["writer_evidence"])
    assert (len(boundary.calls), len(boundary.isolations)) == calls_before
    assert not f["writer_evidence"].exists()


@pytest.mark.parametrize("fault,last_stage", [("image-identity", "image"), ("version", "version")])
def test_unverified_binary_or_version_never_manufactures_native_envelope(tmp_path, monkeypatch, fault, last_stage):
    f = fixture_at(tmp_path)
    before = mutable_state(f)
    boundary = NativeBoundary(f, monkeypatch, fault=fault)
    with pytest.raises(ValueError):
        qualify(f)
    assert boundary.calls[-1]["stage"] == last_stage
    assert not (f["loading_evidence"] / "qualification.json").exists()
    assert mutable_state(f) == before


def test_uncertain_cleanup_retains_candidate_observations_and_prevents_reset(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch, fault="cleanup")
    receipt = qualify(f)
    assert next(row for row in receipt["checks"] if row["id"] == "termination")["status"] == "failed"
    assert (Path(f["roots"]["workspace"]) / "smoke-change.txt").exists()
    assert boundary.isolations == ["isolation-before"]
    restoration = json.loads((f["loading_evidence"] / "restoration.json").read_text(encoding="utf-8"))
    assert restoration["status"] == "not_attempted"
    assert not f["writer_evidence"].exists()


def test_successful_bound_loading_continues_writer_once_without_restoring_its_output(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch)
    receipt = qualify(f)
    derived, _ = package_loading(f, receipt)
    assert derived["host"]["isolation"]["probe_receipt"] is None
    result = run_suite_writer(derived, f["suite_root"], "normalize", f["arm"], f["roots"], f["writer_evidence"])
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery", "smoke", "image", "version", "writer"]
    assert len(boundary.isolations) == 3
    assert result["suite_sha256"] == digest(derived) != digest(f["suite"])
    assert result["process"] == boundary.calls[-1]["result"]["process"]
    assert result["isolation"]["qualification_seconds"] == 0.5 != result["process"]["seconds"]
    assert (Path(f["roots"]["workspace"]) / "writer-result.txt").read_text(encoding="utf-8") == "Synthetic writer change"
    calls_before = len(boundary.calls), len(boundary.isolations)
    with pytest.raises(ValueError, match="^input-exists:"):
        run_suite_writer(derived, f["suite_root"], "normalize", f["arm"], f["roots"], f["writer_evidence"])
    assert (len(boundary.calls), len(boundary.isolations)) == calls_before


def test_changed_frozen_product_between_native_readbacks_prevents_smoke(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch, fault="immutable-input")
    with pytest.raises(ValueError):
        qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image", "version"]
    assert not (f["loading_evidence"] / "qualification.json").exists()
    assert not f["writer_evidence"].exists()


def test_derived_loading_bundle_tampering_is_rejected_before_any_writer_boundary(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch)
    receipt = qualify(f)
    derived, bundle = package_loading(f, receipt)
    (bundle / "observations.json").write_text('{"changed":"bound raw evidence"}\n', encoding="utf-8")
    calls_before = len(boundary.calls), len(boundary.isolations)
    with pytest.raises(ValueError):
        run_suite_writer(derived, f["suite_root"], "normalize", f["arm"], f["roots"], f["writer_evidence"])
    assert (len(boundary.calls), len(boundary.isolations)) == calls_before
    assert not f["writer_evidence"].exists()


NONLOCAL_REQUIREMENTS = REQUIREMENTS.replace("restrict_to_allowed_sources = true",
                                            "restrict_to_allowed_sources = false")


class IncompleteObservationBoundary(NativeBoundary):
    """Change only returned observations, keeping command and budget checks real."""
    def owned(self, profile, layout, argv, *, cwd, env, prompt, timeout, output_limit, evidence_dir):
        result = super().owned(profile, layout, argv, cwd=cwd, env=env, prompt=prompt,
                               timeout=timeout, output_limit=output_limit, evidence_dir=evidence_dir)
        stage = self.calls[-1]["stage"]
        if self.fault == "missing-discovery" and stage == "discovery":
            listing = json.loads(result["process"]["stdout"])
            listing["installed"] = []
            result["process"]["stdout"] = json.dumps(listing)
        elif self.fault == "nonlocal-policy" and stage == "image":
            observation = json.loads(result["process"]["stdout"])
            observation["requirements_toml"] = NONLOCAL_REQUIREMENTS
            observation["requirements_sha256"] = hashlib.sha256(NONLOCAL_REQUIREMENTS.encode()).hexdigest()
            result["process"]["stdout"] = json.dumps(observation)
        # Retain the final synthetic response before returning control to the caller.
        result["process"]["stdout_bytes"] = len(result["process"]["stdout"].encode("utf-8"))
        self.calls[-1]["result"] = deepcopy(result)
        command_path = Path(evidence_dir) / "docker-000.json"
        command = json.loads(command_path.read_text(encoding="utf-8"))
        command["process"] = deepcopy(result["process"])
        save(command_path, command)
        save(Path(evidence_dir) / "owned-result.json", result)
        return result

    def smoke(self, process, prompt):
        super().smoke(process, prompt)
        if self.fault == "no-child-events":
            events = [json.loads(line) for line in process["stdout"].splitlines()]
            events = [event for event in events if event["item"]["type"] != "collab_tool_call"]
            process["stdout"] = "\n".join(json.dumps(event) for event in events) + "\n"


def test_missing_current_discovery_stops_smoke_and_receipts_the_actual_discovery(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = IncompleteObservationBoundary(f, monkeypatch, fault="missing-discovery")
    receipt = qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery"]
    checks = {row["id"]: row["status"] for row in receipt["checks"]}
    assert checks["native-discovery"] == "failed"
    assert receipt["execution"]["argv"] == boundary.calls[-1]["argv"]
    assert receipt["execution"]["returncode"] == boundary.calls[-1]["result"]["process"]["returncode"] == 0
    assert receipt["execution"]["executable_sha256"] == f["runtime"]["host_executable_sha256"]
    assert receipt["execution"]["version"] == f["runtime"]["host_version"]
    execution = json.loads((f["loading_evidence"] / "loading-execution.json").read_text(encoding="utf-8"))
    assert execution["invocations"][-1]["id"] == "discovery"
    for key in ("started_at", "ended_at"):
        assert receipt["execution"][key] == execution["invocations"][-1][key]
    assert mutable_state(f) == before and mutable_ids(f) == identities
    restoration = json.loads((f["loading_evidence"] / "restoration.json").read_text(encoding="utf-8"))
    assert restoration["status"] == "passed"
    assert not f["writer_evidence"].exists()


def test_declared_subagents_require_observed_child_completion_in_actual_smoke(tmp_path, monkeypatch):
    f = fixture_at(tmp_path, subagents=True)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = IncompleteObservationBoundary(f, monkeypatch, fault="no-child-events")
    receipt = qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery", "smoke"]
    checks = {row["id"]: row["status"] for row in receipt["checks"]}
    assert checks["subagent-execution"] == "failed"
    assert all(checks[key] == "passed" for key in
               ("native-discovery", "entry-routing", "task-read-write", "private-access-denied"))
    assert receipt["execution"]["returncode"] == 0
    assert mutable_state(f) == before and mutable_ids(f) == identities
    assert not f["writer_evidence"].exists()


def test_matching_image_policy_hash_does_not_authorize_nonlocal_marketplaces(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    # Bind a different declared policy consistently before entering the public API.
    # Static runtime shape cannot replace the actual image's semantic policy check.
    f["runtime"]["requirements_sha256"] = hashlib.sha256(NONLOCAL_REQUIREMENTS.encode()).hexdigest()
    save(f["native"] / "runtime.json", f["runtime"])
    f["reservation"]["runtime_sha256"] = byte_hash(f["native"] / "runtime.json")
    save(f["reservation_path"], f["reservation"])
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = IncompleteObservationBoundary(f, monkeypatch, fault="nonlocal-policy")
    with pytest.raises(ValueError):
        qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image"]
    observed = json.loads(boundary.calls[0]["result"]["process"]["stdout"])
    assert observed["requirements_sha256"] == f["runtime"]["requirements_sha256"]
    assert observed["requirements_sha256"] == hashlib.sha256(observed["requirements_toml"].encode()).hexdigest()
    assert not (f["loading_evidence"] / "qualification.json").exists()
    assert mutable_state(f) == before and mutable_ids(f) == identities
    assert not f["writer_evidence"].exists()




def test_bootstrap_cannot_claim_the_writer_phase(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch)
    before = tree_state(f["native"].parent), tree_state(f["suite_root"])
    with pytest.raises(ValueError):
        qualify_native_loading(f["suite"], f["suite_root"], "normalize", f["arm"],
                               f["roots"], f["writer_evidence"])
    assert boundary.calls == [] and boundary.isolations == []
    assert not f["loading_evidence"].exists() and not f["writer_evidence"].exists()
    assert not (f["native"].parent / "private/execution.lock").exists()
    assert (tree_state(f["native"].parent), tree_state(f["suite_root"])) == before


def test_ordinary_writer_cannot_claim_an_absent_loading_phase(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch)
    receipt = qualify(f)
    derived, _ = package_loading(f, receipt)
    # Preserve the original raw bytes while making the wrong destination absent;
    # an ordinary existing-path collision must not satisfy this phase control.
    retained = f["loading_evidence"].with_name("retained-bootstrap")
    f["loading_evidence"].rename(retained)
    before = tree_state(f["native"].parent), tree_state(f["suite_root"])
    calls_before = len(boundary.calls), len(boundary.isolations)
    with pytest.raises(ValueError):
        run_suite_writer(derived, f["suite_root"], "normalize", f["arm"],
                         f["roots"], f["loading_evidence"])
    assert (len(boundary.calls), len(boundary.isolations)) == calls_before
    assert not f["loading_evidence"].exists() and not f["writer_evidence"].exists()
    assert not (f["native"].parent / "private/execution.lock").exists()
    assert (tree_state(f["native"].parent), tree_state(f["suite_root"])) == before




@pytest.mark.parametrize("field,value", [("status", "unverified"), ("container_id", "f" * 64),
                                          ("reason", "Contradictory retained lifecycle observation")])
def test_rehashed_raw_lifecycle_cannot_contradict_the_derived_loading_invocation(tmp_path, monkeypatch, field, value):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch)
    receipt = qualify(f)
    derived, bundle = package_loading(f, receipt)
    raw_path = bundle / "smoke/lifecycle.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert set(raw) == {"container_id", "owner", "status", "reason", "evidence"}
    execution_bytes = (bundle / "loading-execution.json").read_bytes()
    raw[field] = value
    save(raw_path, raw)
    receipt_path = f["suite_root"] / derived["arms"][f["arm"]]["loading"]["receipt"]
    outer = json.loads(receipt_path.read_text(encoding="utf-8"))
    path = raw_path.relative_to(f["suite_root"]).as_posix()
    matches = [row for row in outer["evidence"] if row["path"] == path]
    assert len(matches) == 1
    matches[0]["sha256"] = byte_hash(raw_path)
    save(receipt_path, outer)
    # Only one retained raw observation and its outer hash were changed; no
    # derived invocation, process, source proof, or loading status was rewritten.
    assert (bundle / "loading-execution.json").read_bytes() == execution_bytes
    before = tree_state(f["native"].parent), tree_state(f["suite_root"])
    calls_before = len(boundary.calls), len(boundary.isolations)
    with pytest.raises(ValueError):
        run_suite_writer(derived, f["suite_root"], "normalize", f["arm"], f["roots"], f["writer_evidence"])
    assert (len(boundary.calls), len(boundary.isolations)) == calls_before
    assert not f["writer_evidence"].exists()
    assert not (f["native"].parent / "private/execution.lock").exists()
    assert (tree_state(f["native"].parent), tree_state(f["suite_root"])) == before




class DisagreeingSmokeBoundary(NativeBoundary):
    def smoke(self, process, prompt):
        super().smoke(process, prompt)
        if self.fault == "action-disagreement":
            events = [json.loads(line) for line in process["stdout"].splitlines()]
            actions = [event["item"] for event in events if event["item"]["id"] == "action"]
            assert len(actions) == 1
            reported = json.loads(actions[0]["aggregated_output"])
            reported.update(task_read=False, private_read_denied=False, private_write_denied=False)
            actions[0]["aggregated_output"] = json.dumps(reported, sort_keys=True) + "\n"
            process["stdout"] = "\n".join(json.dumps(event) for event in events) + "\n"
        elif self.fault == "output-alias":
            controls = list(Path(self.fixture["roots"]["workspace"]).glob("forge-loading-*"))
            assert len(controls) == 1
            output = controls[0] / "output.json"
            original = output.read_bytes()
            target = output.with_name("retained-output.json")
            output.rename(target)
            output.symlink_to(target.name)
            assert output.is_symlink() and output.read_bytes() == original


def test_action_stdout_disagreement_cannot_be_replaced_by_successful_host_readback(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = DisagreeingSmokeBoundary(f, monkeypatch, fault="action-disagreement")
    receipt = qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery", "smoke"]
    checks = {row["id"]: row["status"] for row in receipt["checks"]}
    assert checks["task-read-write"] == checks["private-access-denied"] == "failed"
    assert checks["native-discovery"] == checks["entry-routing"] == "passed"
    assert receipt["execution"]["returncode"] == 0
    observations = json.loads((f["loading_evidence"] / "observations.json").read_text(encoding="utf-8"))
    assert all(observations["task_output"]["value"][key] is True for key in
               ("task_read", "private_read_denied", "private_write_denied"))
    assert mutable_state(f) == before and mutable_ids(f) == identities
    assert not f["writer_evidence"].exists()


def test_contained_output_symlink_is_not_regular_host_observation(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = DisagreeingSmokeBoundary(f, monkeypatch, fault="output-alias")
    receipt = qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery", "smoke"]
    checks = {row["id"]: row["status"] for row in receipt["checks"]}
    assert checks["task-read-write"] == "failed"
    assert receipt["execution"]["returncode"] == 0
    assert mutable_state(f) == before and mutable_ids(f) == identities
    assert not f["writer_evidence"].exists()




def test_rehashed_raw_start_exit_cannot_contradict_retained_loading_process(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    boundary = NativeBoundary(f, monkeypatch)
    receipt = qualify(f)
    derived, bundle = package_loading(f, receipt)
    raw_path = bundle / "smoke/docker-000.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["process"]["returncode"] == 0
    unchanged = {name: (bundle / name).read_bytes() for name in
                 ("smoke/owned-result.json", "smoke/lifecycle.json", "loading-execution.json")}
    raw["process"]["returncode"] = 7
    save(raw_path, raw)
    receipt_path = f["suite_root"] / derived["arms"][f["arm"]]["loading"]["receipt"]
    outer = json.loads(receipt_path.read_text(encoding="utf-8"))
    path = raw_path.relative_to(f["suite_root"]).as_posix()
    matches = [row for row in outer["evidence"] if row["path"] == path]
    assert len(matches) == 1
    matches[0]["sha256"] = byte_hash(raw_path)
    save(receipt_path, outer)
    assert {name: (bundle / name).read_bytes() for name in unchanged} == unchanged
    before = tree_state(f["native"].parent), tree_state(f["suite_root"])
    calls_before = len(boundary.calls), len(boundary.isolations)
    with pytest.raises(ValueError):
        run_suite_writer(derived, f["suite_root"], "normalize", f["arm"], f["roots"], f["writer_evidence"])
    assert (len(boundary.calls), len(boundary.isolations)) == calls_before
    assert not f["writer_evidence"].exists()
    assert not (f["native"].parent / "private/execution.lock").exists()
    assert (tree_state(f["native"].parent), tree_state(f["suite_root"])) == before




class OversizedControlBoundary(NativeBoundary):
    def smoke(self, process, prompt):
        super().smoke(process, prompt)
        controls = list(Path(self.fixture["roots"]["workspace"]).glob("forge-loading-*"))
        assert len(controls) == 1
        output = controls[0] / "output.json"
        original = output.read_bytes()
        output.write_bytes(original + b" " * (65537 - len(original)))
        assert output.stat().st_size == 65537
        assert json.loads(output.read_bytes()) == json.loads(original)


def test_oversized_valid_control_json_fails_before_acceptance_and_restores_roots(tmp_path, monkeypatch):
    f = fixture_at(tmp_path)
    before, identities = mutable_state(f), mutable_ids(f)
    boundary = OversizedControlBoundary(f, monkeypatch)
    receipt = qualify(f)
    assert [call["stage"] for call in boundary.calls] == ["image", "version", "discovery", "smoke"]
    checks = {row["id"]: row["status"] for row in receipt["checks"]}
    assert checks["task-read-write"] == "failed"
    assert receipt["execution"]["returncode"] == 0
    assert mutable_state(f) == before and mutable_ids(f) == identities
    assert not f["writer_evidence"].exists()
