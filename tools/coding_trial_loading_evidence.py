"""Native loading observations and bound-bundle readback; never executes code."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shlex
import tomllib

from coding_trial_inventory import fingerprint, relative_path
from coding_trial_native import check_native_events, entry_read_command
from coding_trial_qualification import _bindings, _instant, _receipt, fields, parse_json, read_bytes, require, sha

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
PROBE_PYTHON = "/usr/local/bin/python3"
POLICY = {"marketplaces": {"restrict_to_allowed_sources": True,
          "allowed_sources": {"evaluator": {"source": "local", "path": "/marketplace"}}}}
BOUNDS = {"image": (15, 65536), "version": (15, 65536), "discovery": (30, 1048576),
          "smoke": (900, 8388608), "writer": (900, 8388608)}


def complete(process):
    return (type(process) is dict and type(process.get("returncode")) is int and process["returncode"] == 0
            and all(process.get(key) is False for key in ("timed_out", "stdout_truncated", "stderr_truncated"))
            and process.get("termination_error") is None
            and isinstance(process.get("stdout"), str) and isinstance(process.get("stderr"), str))


def loading_configuration(suite):
    configuration = deepcopy(suite)
    for arm in configuration["arms"].values():
        arm["loading"]["receipt"] = None
    return fingerprint(configuration)


def image_observation(process, runtime):
    require(complete(process), "Incomplete native image readback")
    value = parse_json(process["stdout"])
    fields(value, "schema executable_sha256 requirements_sha256 requirements_toml", "Image observation")
    require(value["schema"] == "coding-trial-native-image/v1"
            and value["executable_sha256"] == runtime["host_executable_sha256"], "Native binary identity changed")
    policy = value["requirements_toml"]
    require(isinstance(policy, str), "Requirements readback must be text")
    require(hashlib.sha256(policy.encode("utf-8")).hexdigest()
            == value["requirements_sha256"] == runtime["requirements_sha256"], "Native requirements bytes changed")
    require(fingerprint(tomllib.loads(policy)) == fingerprint(POLICY), "Native requirements must enforce local-only policy")
    return value


def version_observation(process, runtime):
    require(complete(process) and process["stdout"].strip() == runtime["host_version"],
            "Native version readback differs")
    return process["stdout"].strip()


def discovery_observation(process, expected_stdout):
    require(complete(process), "Incomplete native discovery")
    observed, expected = parse_json(process["stdout"]), parse_json(expected_stdout)
    for value in (observed, expected):
        fields(value, "installed available", "Native listing")
        require(type(value["installed"]) is list, "Invalid native installed list")
        for plugin in value["installed"]:
            require(type(plugin) is dict, "Invalid native installed entry")
            if "marketplaceSource" in plugin:
                require(plugin.pop("marketplaceSource") == {"sourceType": "local", "source": "/marketplace"},
                        "Native marketplace source differs")
    require(fingerprint(observed) == fingerprint(expected), "Native installed/enabled product readback differs")


def smoke_commands(runtime, nonce, evidence_dir, subagents):
    require(isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{32}", nonce), "Invalid loading nonce")
    base = "/workspace/forge-loading-" + nonce
    entry = entry_read_command(runtime)
    action = shlex.join([PROBE_PYTHON, "-I", "-B", "-c", ACTION_SCRIPT, base + "/input.txt",
                         base + "/output.json", str(Path(evidence_dir) / "private-control.txt"), nonce])
    parts = ["Qualification only; do not implement the task. Run each exact command using shell /bin/sh with login false."]
    if entry is not None:
        parts.append("Read the selected installed entry:\n" + entry)
    parts.append("Perform the controlled task and private-access observation:\n" + action)
    if subagents:
        parts.append("Spawn one new child to report readiness, then wait until that same child has completed.")
    return entry, action, "\n\n".join(parts)


def action_output(nonce):
    value = {"nonce": nonce, "task_read": True, "private_read_denied": True, "private_write_denied": True}
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


def isolation_observation(folder, returned, expected):
    proof, _ = _receipt(folder, "qualification.json", "isolation", "prospective", (),
                         extra_checks=("network-denied",) if expected["network"] == "none" else ("network-mode-confirmed",))
    require(proof == returned, "Current isolation return differs from its retained receipt")
    _bindings(proof, expected, hashes=("docker_executable_sha256",), identifiers=("docker_version",))
    require(proof["execution"]["executable_sha256"] == proof["bindings"]["docker_executable_sha256"]
            and proof["execution"]["version"] == proof["bindings"]["docker_version"], "Isolation execution identity differs")
    return proof


def loading_checks(runtime, invocations, observations, restoration, *, entry_bytes, discovery_stdout, isolation_ok, subagents):
    calls = {item["id"]: item for item in invocations}
    discovered = False
    try:
        image_observation(calls["image"]["process"], runtime)
        version_observation(calls["version"]["process"], runtime)
        discovery_observation(calls["discovery"]["process"], discovery_stdout)
        discovered = True
    except (KeyError, TypeError, ValueError):
        pass
    events = check_native_events(calls.get("smoke", {}).get("process"), entry_command=observations["entry_command"],
                                entry_bytes=entry_bytes, action_command=observations["action_command"],
                                subagents=subagents, action_bytes=action_output(observations["nonce"]))
    output = observations["task_output"]
    wanted = parse_json(action_output(observations["nonce"]))
    action_passed = events["required-tools"]["status"] == "passed"
    output_passed = (type(output) is dict and output.get("path") == f"forge-loading-{observations['nonce']}/output.json"
                     and fingerprint(output.get("value")) == fingerprint(wanted))
    checks = {"native-discovery": discovered,
              "entry-routing": events["entry-routing"]["status"] in {"passed", "not_applicable"},
              "required-tools": action_passed,
              "task-read-write": action_passed and output_passed and not observations["errors"],
              "private-access-denied": action_passed and output_passed and observations["private_unchanged"] is True,
              "termination": isolation_ok and restoration["status"] == "passed"
                and restoration["anchors_unchanged"] is True and restoration["immutable_unchanged"] is True
                and bool(invocations) and all(item["lifecycle"]["status"] == "verified" for item in invocations)}
    if events["subagent-execution"]["status"] != "not_applicable":
        checks["subagent-execution"] = events["subagent-execution"]["status"] == "passed"
    return checks, events


def native_usage(process):
    if not complete(process):
        return None
    try:
        events = [parse_json(line) for line in process["stdout"].splitlines() if line.strip()]
        require(all(type(event) is dict for event in events), "Invalid native event")
        usage = [event["usage"] for event in events if event.get("type") == "turn.completed"
                 and type(event.get("usage")) is dict]
    except (TypeError, ValueError):
        return None
    if not usage:
        return None
    totals = {}
    for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
        values = [item.get(key) for item in usage]
        totals[key] = sum(values) if all(type(value) is int and value >= 0 for value in values) else None
    return {"source": "codex exec JSONL", "usage": usage, "totals": totals, "api_retries": None,
            "command_seconds": None, "limits": "Usage is reported by the native CLI; command timing and backend identity are unknown."}


def _verify_loading_bundle(suite, root, arm_id, runtime, layout, reservation, reservation_sha256,
                           *, discovery, entry_bytes, isolation_bindings):
    """Rederive a current loading pass from its bound raw bundle, not a status marker."""
    proof, evidence = _receipt(root, suite["arms"][arm_id]["loading"]["receipt"], "loading", "prospective", (),
                              extra_checks=("subagent-execution",) if suite["host"]["capabilities"]["subagents"] else ())
    require(proof["bindings"]["host_executable_sha256"] == runtime["host_executable_sha256"], "Loading native binary binding differs")
    matches = [name for name in evidence if Path(name).name == "loading-execution.json"]
    require(len(matches) == 1, "Loading receipt needs one execution bundle")
    bundle = (root / matches[0]).parent
    local = {}
    for name, raw in evidence.items():
        path = (root / name).resolve(strict=True)
        require(path.is_relative_to(bundle), "Loading evidence leaves its bundle")
        local[path.relative_to(bundle).as_posix()] = raw
    def record(name):
        relative_path(name)
        require(name in local, f"Unbound loading evidence: {name}")
        return parse_json(local[name])
    control = record("control.json")
    require(control == {"schema": "coding-trial-native-control/v1", "reservation_sha256": reservation_sha256,
                        "initial_attempt_sha256": reservation["attempt_record"]["sha256"],
                        "suite_source_sha256": reservation["suite_source_sha256"]}, "Loading reservation binding differs")
    execution = record("loading-execution.json")
    fields(execution, "schema loading_configuration_sha256 task arm runtime_sha256 layout_sha256 host_executable_sha256 "
           "image_digest profile_sha256 nonce invocations observations lifecycle", "Loading execution")
    expected = {"schema": "coding-trial-native-loading-execution/v1", "loading_configuration_sha256": loading_configuration(suite),
                "task": reservation["task"], "arm": arm_id, "runtime_sha256": reservation["runtime_sha256"],
                "layout_sha256": fingerprint({key: layout[key] for key in ("mounts", "user", "network")}),
                **{key: runtime[key] for key in ("host_executable_sha256", "image_digest", "profile_sha256")}}
    require(all(execution[key] == value for key, value in expected.items()), "Loading execution identity is stale")
    entry, action, prompt = smoke_commands(runtime, execution["nonce"], Path(reservation["roots"]["native_runtime"]).parent / "private/loading",
                                           suite["host"]["capabilities"]["subagents"])
    calls = execution["invocations"]
    require(type(calls) is list and [call.get("id") for call in calls] == ["image", "version", "discovery", "smoke"],
            "Incomplete loading invocation sequence")
    commands = [[PROBE_PYTHON, "-I", "-B", "-c", IMAGE_READBACK, runtime["host_executable"]],
                [runtime["host_executable"], "--version"], discovery["argv"], runtime["launch"]["argv"]]
    original = Path(reservation["roots"]["native_runtime"]).parent / "private/loading"
    profile = parse_json(read_bytes(root, suite["host"]["isolation"]["profile"]))
    for call, command in zip(calls, commands):
        fields(call, "id argv environment cwd prompt timeout_seconds output_bytes started_at ended_at process container_id lifecycle", "Native invocation")
        require(call["argv"] == command and call["environment"] == runtime["launch"]["environment"]
                and call["cwd"] == "/workspace" and call["prompt"] == (prompt if call["id"] == "smoke" else None)
                and (call["timeout_seconds"], call["output_bytes"]) == BOUNDS[call["id"]], "Loading command differs")
        require(_instant(call["started_at"]) <= _instant(call["ended_at"]) and complete(call["process"]),
                "Loading invocation is incomplete")
        require(isinstance(call["container_id"], str) and re.fullmatch(r"[0-9a-f]{64}", call["container_id"]), "Invalid owned container ID")
        raw_result = record(call["id"] + "-result.json")
        require(raw_result["process"] == call["process"] and raw_result["container_id"] == call["container_id"], "Loading process record differs")
        fields(call["lifecycle"], "status reason evidence", "Invocation lifecycle")
        require(call["lifecycle"]["status"] == raw_result["lifecycle"]["status"] == "verified"
                and call["lifecycle"]["reason"] == raw_result["lifecycle"]["reason"], "Loading lifecycle differs")
        references = call["lifecycle"]["evidence"]
        require(type(references) is list and references and len(references) == len(set(references)), "Missing lifecycle evidence")
        require(references == [Path(path).relative_to(original).as_posix() for path in raw_result["lifecycle"]["evidence"]],
                "Lifecycle evidence was reinterpreted")
        lifecycle = record(call["id"] + "/lifecycle.json")
        fields(lifecycle, "container_id owner status reason evidence", "Raw invocation lifecycle")
        require(isinstance(lifecycle["owner"], str) and re.fullmatch(r"[0-9a-f]{32}", lifecycle["owner"]),
                "Invalid lifecycle owner")
        require(lifecycle["container_id"] == call["container_id"]
                and all(lifecycle[key] == raw_result["lifecycle"][key] for key in ("status", "reason", "evidence")),
                "Raw lifecycle contradicts its invocation")
        start_command = [profile["docker_executable"], "--host", profile["endpoint"], "--config",
                         str(original / call["id"] / "docker-config"),
                         "start", "--attach", "--interactive", call["container_id"]]
        starts = []
        for name in references:
            relative_path(name)
            require(name in local, "Unbound lifecycle reference")
            observation = record(name)
            require(type(observation) is dict, "Invalid raw Docker observation")
            if observation.get("argv") == start_command:
                starts.append(observation)
        require(len(starts) == 1, "Loading needs exactly one referenced Docker start")
        start = starts[0]
        fields(start, "argv environment process started_at ended_at", "Raw Docker start")
        require(start["environment"] == {} and start["process"] == call["process"]
                and _instant(start["started_at"]) <= _instant(start["ended_at"]),
                "Raw Docker start contradicts its invocation")
        require(record(call["id"] + ".json") == call, "Retained invocation differs")
    observations = record("observations.json")
    fields(observations, "schema nonce entry_command action_command task_output private_unchanged events mutable_sha256 errors", "Loading observations")
    require(observations["schema"] == "coding-trial-native-observations/v1" and observations["nonce"] == execution["nonce"]
            and observations["entry_command"] == entry and observations["action_command"] == action
            and observations["errors"] == [], "Loading action observations differ")
    fields(observations["mutable_sha256"], "workspace workspace_git home codex generated", "Mutable observations")
    for name, digest in observations["mutable_sha256"].items():
        if name != "workspace_git" or digest is not None:
            sha(digest)
    readback = record("task-output-readback.json")
    fields(readback, "path raw", "Task output readback")
    raw = readback["raw"].encode("utf-8")
    output = observations["task_output"]
    require(output == {"path": readback["path"], "sha256": hashlib.sha256(raw).hexdigest(), "value": parse_json(raw)},
            "Task output readback differs")
    require(local["private-control.txt"] == execution["nonce"].encode("utf-8")
            and observations["private_unchanged"] is True, "Private canary differs")
    restoration = record("restoration.json")
    fields(restoration, "schema status inventory_sha256 anchors_unchanged immutable_unchanged reason", "Restoration")
    require(restoration["schema"] == "coding-trial-native-restoration/v1"
            and restoration["inventory_sha256"] == {name: None if snapshot is None else snapshot["inventory_sha256"]
                                                   for name, snapshot in reservation["snapshots"].items()}, "Restored inventory differs")
    for name in ("isolation-before", "isolation-after"):
        receipt = record(name + "/qualification.json")
        isolation_observation(bundle / name, receipt, isolation_bindings)
        require(all(name + "/" + item["path"] in local for item in receipt["evidence"]), "Unbound isolation evidence")
    checks, events = loading_checks(runtime, calls, observations, restoration, entry_bytes=entry_bytes,
                                   discovery_stdout=discovery["stdout"], isolation_ok=True,
                                   subagents=suite["host"]["capabilities"]["subagents"])
    require(events == observations["events"] and all(checks.values()), "Raw loading observations do not establish a pass")
    require(set(execution["observations"]) == set(checks), "Loading check set differs")
    for name, check in execution["observations"].items():
        fields(check, "status evidence", "Loading check observation")
        require(check["status"] == "passed" and check["evidence"]
                and all(path in local for path in check["evidence"]), "Unbound loading check")
    fields(execution["lifecycle"], "status evidence", "Loading lifecycle")
    require(execution["lifecycle"] == {"status": "verified", "evidence": ["restoration.json",
            *[path for call in calls for path in call["lifecycle"]["evidence"]]]}, "Loading lifecycle was not verified")
    last = calls[-1]
    require(proof["execution"] == {"argv": last["argv"], "executable_sha256": runtime["host_executable_sha256"],
             "version": version_observation(calls[1]["process"], runtime), "platform": isolation_bindings["platform"],
             "started_at": last["started_at"], "ended_at": last["ended_at"], "returncode": last["process"]["returncode"]},
            "Loading envelope does not identify its actual native invocation")
    return proof


def verify_loading_bundle(suite, root, arm_id, runtime, layout, reservation, reservation_sha256,
                          *, discovery, entry_bytes, isolation_bindings):
    try:
        return _verify_loading_bundle(suite, root, arm_id, runtime, layout, reservation, reservation_sha256,
                                      discovery=discovery, entry_bytes=entry_bytes, isolation_bindings=isolation_bindings)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"loading-unqualified: Invalid bound native evidence: {exc}") from exc
