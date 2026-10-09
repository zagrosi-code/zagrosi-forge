"""Native loading admission and owned execution for prepared suite attempts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import uuid

from coding_trial_docker import run_owned
from coding_trial_inventory import fingerprint, inventory
from coding_trial_isolation import ADAPTER_SOURCES, derive_layout, qualify_profile
from coding_trial_loading_evidence import (
    BOUNDS, IMAGE_READBACK, PROBE_PYTHON, action_output, discovery_observation, image_observation,
    isolation_observation, loading_checks, loading_configuration, native_usage, smoke_commands,
    verify_loading_bundle, version_observation,
)
from coding_trial_native import _regular_bytes, _runtime_roots, check_native_events, read_native_runtime
from coding_trial_qualification import file_sha, parse_json, read_bytes, require
from coding_trial_reservation import _check_reservation, _reserved_attempt, _restore_reservation, _save_bytes, _save_record


def _admit(suite, suite_root, task_id, arm_id, roots, auth_file, *, loading_required):
    """Reject credential aliases before any suite/resource content is read."""
    try:
        host = suite["host"]
        require(host["adapter"] == "codex" and suite["purpose"] == "prospective",
                "Native loading requires a prospective Codex suite")
        root = Path(suite_root).resolve(strict=True)
        _runtime_roots(roots, host["credentials"], auth_file, root)
        task, arm = suite["tasks"][task_id], suite["arms"][arm_id]
        isolation = host["isolation"]
        environment = task["dependencies"]["environment"]
        admission, loading = task["admission"], arm["loading"]["receipt"]
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"loading-unqualified: {exc}") from exc
    if isolation is None:
        raise ValueError("unsupported-profile: Native execution requires Docker isolation")
    if environment is None:
        raise ValueError("environment-unqualified: Missing prepared environment evidence")
    if admission is None:
        raise ValueError("task-unqualified: Missing independent task evidence")
    runtime = read_native_runtime(suite, root, task_id, arm_id, roots, auth_file=auth_file)
    if loading_required and loading is None:
        raise ValueError("loading-unqualified: Missing native loading evidence")
    layout = derive_layout(suite, root, task_id, arm_id, roots, role="writer", auth_file=auth_file)
    return runtime, layout


def _resources(suite, root):
    names, receipts = {suite["host"]["isolation"]["profile"]}, []
    receipts.append(suite["host"]["isolation"]["probe_receipt"])
    for task in suite["tasks"].values():
        source = task["source"]
        names.update((source["export"], task["brief"]))
        if source["preparation"] is not None:
            names.add(source["preparation"]["evidence"])
        if task["clarifications"] is not None:
            names.add(task["clarifications"])
        receipts.extend((task["dependencies"]["environment"], task["admission"]))
        for command in [*task["checks"]["native"], task["checks"]["oracle"], task["checks"]["worker"]]:
            names.update(command["support"])
            if command["entry"] is not None:
                names.add(command["entry"])
    for arm in suite["arms"].values():
        names.add(arm["entry"])
        if arm["product"] is not None:
            names.add(arm["product"]["payload"])
        receipts.append(arm["loading"]["receipt"])
    for name in receipts:
        if name is not None:
            names.add(name)
            names.update(item["path"] for item in parse_json(read_bytes(root, name))["evidence"])
    return tuple(sorted(names))


def _immutable(suite, root, roots):
    native = Path(roots["native_runtime"])
    folders = [Path(roots[name]) for name in ("product", "public")] + [native / "plugins", native / "marketplace"]
    result = {"suite": fingerprint(suite), "inputs": fingerprint(inventory(root, included=_resources(suite, root)))}
    for folder in [root, *folders]:
        require(folder.resolve(strict=True) == folder, "Immutable directory was redirected")
        info, parent = folder.stat(), folder.parent.stat()
        result[str(folder)] = (info.st_dev, info.st_ino, info.st_mode, parent.st_dev, parent.st_ino)
        if folder != root:
            result[str(folder) + "/inventory"] = fingerprint(inventory(folder))
    result["runtime"] = file_sha(native, "runtime.json")
    result["preparation"] = file_sha(native.parent / "private", "native-preparation.json")
    return result


def _guard(state, suite, root, roots, frozen):
    _check_reservation(state)
    require(_immutable(suite, root, roots) == frozen, "loading-unqualified: Immutable native inputs changed")


def _isolation_bindings(suite, root, profile, layout):
    source = Path(__file__).resolve().parents[1]
    expected = {key: profile[key] for key in ("context", "endpoint", "server_id", "server_version", "image_digest", "platform", "network")}
    expected.update(profile_sha256=file_sha(root, suite["host"]["isolation"]["profile"]),
                    adapter_sha256=fingerprint(inventory(source, included=ADAPTER_SOURCES)),
                    probe_sha256=fingerprint(inventory(source, included=("tools/coding_trial_isolation.py", "tools/coding_trial_docker.py"))),
                    layout_sha256=fingerprint({key: layout[key] for key in ("mounts", "user", "network")}),
                    limits_sha256=fingerprint(profile["limits"]))
    return expected


def _current_isolation(state, name, suite, root, layout, roots, auth_file, bindings):
    folder = state["evidence"] / name
    receipt = qualify_profile(suite, root, layout, folder, roots=roots, auth_file=auth_file)
    try:
        return isolation_observation(folder, receipt, bindings)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"isolation-unqualified: Current native exposure failed: {exc}; evidence: {folder}") from exc


def _call(state, name, profile, layout, runtime, argv, *, prompt=None):
    _check_reservation(state)
    folder = state["evidence"] / name
    folder.mkdir()
    started = datetime.now(timezone.utc).isoformat()
    timeout, limit = BOUNDS[name]
    result = run_owned(profile, layout, argv, cwd="/workspace", env=runtime["launch"]["environment"],
                       prompt=prompt, timeout=timeout, output_limit=limit, evidence_dir=folder)
    ended = datetime.now(timezone.utc).isoformat()
    _save_record(state, name + "-result.json", result)
    lifecycle = dict(result["lifecycle"])
    lifecycle["evidence"] = [Path(path).relative_to(state["evidence"]).as_posix() for path in lifecycle["evidence"]]
    record = {"id": name, "argv": argv, "environment": runtime["launch"]["environment"], "cwd": "/workspace",
              "prompt": prompt, "timeout_seconds": timeout, "output_bytes": limit, "started_at": started,
              "ended_at": ended, "process": result["process"], "container_id": result["container_id"], "lifecycle": lifecycle}
    _save_record(state, "writer-invocation.json" if name == "writer" else name + ".json", record)
    return record


def _control_record(state):
    reservation = state["record"]
    _save_record(state, "control.json", {"schema": "coding-trial-native-control/v1",
        "reservation_sha256": state["reservation_sha256"], "initial_attempt_sha256": reservation["attempt_record"]["sha256"],
        "suite_source_sha256": reservation["suite_source_sha256"]})


def _make_controls(state, roots, nonce):
    workspace = Path(roots["workspace"])
    handle = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        expected = state["record"]["anchors"]["workspace"]
        info = os.fstat(handle)
        require((info.st_dev, info.st_ino, info.st_mode) == (expected["device"], expected["inode"], expected["mode"]),
                "Workspace changed before control creation")
        name = "forge-loading-" + nonce
        os.mkdir(name, dir_fd=handle)
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle)
        try:
            target = os.open("input.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=child)
            with os.fdopen(target, "wb") as stream:
                stream.write(nonce.encode("utf-8"))
        finally:
            os.close(child)
    finally:
        os.close(handle)
    _save_bytes(state, "private-control.txt", nonce.encode("utf-8"))


def _mutable_digests(state):
    result, errors = {}, []
    for name in ("workspace", "workspace_git", "home", "codex", "generated"):
        folder = Path(state["record"]["anchors"]["workspace" if name == "workspace_git" else name]["path"])
        if name == "workspace_git":
            folder = folder / ".git"
        try:
            result[name] = (None if name == "workspace_git" and not os.path.lexists(folder)
                            else fingerprint(inventory(folder, excluded=(".git",) if name == "workspace" else ())))
        except (OSError, ValueError, RuntimeError) as exc:
            result[name] = None
            errors.append(f"{name}: {exc}")
    return result, errors


def _observations(state, roots, nonce, entry, action, calls, entry_bytes, subagents):
    digests, errors = _mutable_digests(state)
    name = "forge-loading-" + nonce + "/output.json"
    output, raw = None, None
    try:
        raw = _regular_bytes(Path(roots["workspace"]), name, max_bytes=65536).decode("utf-8")
        output = {"path": name, "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(), "value": parse_json(raw)}
    except (OSError, ValueError, UnicodeError) as exc:
        errors.append(str(exc))
    _save_record(state, "task-output-readback.json", {"path": name, "raw": raw})
    try:
        unchanged = read_bytes(state["evidence"], "private-control.txt") == nonce.encode("utf-8")
    except (OSError, ValueError):
        unchanged = False
    smoke = next((item["process"] for item in calls if item["id"] == "smoke"), None)
    events = check_native_events(smoke, entry_command=entry, entry_bytes=entry_bytes, action_command=action,
                                 subagents=subagents, action_bytes=action_output(nonce))
    value = {"schema": "coding-trial-native-observations/v1", "nonce": nonce, "entry_command": entry,
             "action_command": action, "task_output": output, "private_unchanged": unchanged,
             "events": events, "mutable_sha256": digests, "errors": errors}
    _save_record(state, "observations.json", value)
    return value


def _restore(state, suite, root, roots, frozen, *, cleanup_verified):
    digests, _ = _mutable_digests(state)
    report = {"schema": "coding-trial-native-restoration/v1", "status": "not_attempted", "inventory_sha256": digests,
              "anchors_unchanged": None, "immutable_unchanged": None, "reason": "Owned lifecycle could not be verified"}
    if cleanup_verified:
        try:
            _guard(state, suite, root, roots, frozen)
            report = _restore_reservation(state)
            _guard(state, suite, root, roots, frozen)
            report.update(immutable_unchanged=True, reason="Owned mutable interiors and immutable identities verified")
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            report.update(status="failed", reason=str(exc))
    _save_record(state, "restoration.json", report)
    return report


def _entry_bytes(runtime, roots):
    return None if runtime["installed"] is None else read_bytes(Path(roots["product"]), runtime["selected_entry"])


def _discovery(runtime, roots):
    control = Path(roots["native_runtime"]).parent / "private"
    return parse_json(read_bytes(control, runtime["preparation"]["path"]))["executions"][-1]


def _loading_receipt(state, suite, arm_id, runtime, profile, bindings, calls, nonce, checks):
    references = {key: ["loading-execution.json", "observations.json"] for key in checks}
    references["native-discovery"] = [name + ".json" for name in ("image", "version", "discovery")]
    references["termination"] = ["restoration.json", "loading-execution.json"]
    for key in ("task-read-write", "private-access-denied"):
        references[key].append("task-output-readback.json")
    references["private-access-denied"].append("private-control.txt")
    existing = inventory(state["evidence"])
    references = {key: [path for path in paths if path == "loading-execution.json" or path in existing] for key, paths in references.items()}
    observed = {key: {"status": "passed" if passed else "failed", "evidence": references[key]} for key, passed in checks.items()}
    lifecycle = {"status": "verified" if checks["termination"] else "failed",
                 "evidence": ["restoration.json", *[path for call in calls for path in call["lifecycle"]["evidence"]]]}
    execution = {"schema": "coding-trial-native-loading-execution/v1", "loading_configuration_sha256": loading_configuration(suite),
                 "task": state["record"]["task"], "arm": arm_id, "runtime_sha256": state["record"]["runtime_sha256"],
                 "layout_sha256": bindings["layout_sha256"], "host_executable_sha256": runtime["host_executable_sha256"],
                 "image_digest": runtime["image_digest"], "profile_sha256": runtime["profile_sha256"], "nonce": nonce,
                 "invocations": calls, "observations": observed, "lifecycle": lifecycle}
    _save_record(state, "loading-execution.json", execution)
    entries = inventory(state["evidence"])
    evidence = [{"path": name, "sha256": item["sha256"]} for name, item in sorted(entries.items()) if item["type"] == "file"]
    last = calls[-1]
    receipt = {"schema": "coding-trial-qualification/v1", "kind": "loading",
        "bindings": {"image_digest": runtime["image_digest"], "host_version": runtime["host_version"],
                     "model": suite["host"]["model"], "effort": suite["host"]["effort"],
                     **{key: runtime[key] for key in ("host_executable_sha256", "product_sha256", "entry_sha256",
                                                    "configuration_sha256", "capabilities_sha256", "profile_sha256")}},
        "checks": [{"id": key, **value} for key, value in sorted(observed.items())],
        "producer": {"name": "coding_trial_loading.qualify_native_loading", "independent": False, "purpose": "actual"},
        "execution": {"argv": last["argv"], "executable_sha256": runtime["host_executable_sha256"],
                      "version": version_observation(calls[1]["process"], runtime), "platform": profile["platform"],
                      "started_at": last["started_at"], "ended_at": last["ended_at"], "returncode": last["process"]["returncode"]},
        "evidence": evidence}
    _save_record(state, "qualification.json", receipt)
    return receipt


def qualify_native_loading(suite, suite_root, task_id, arm_id, roots, evidence_dir, *, auth_file=None):
    """Observe actual native loading, then restore only the reserved mutable roots."""
    runtime, layout = _admit(suite, suite_root, task_id, arm_id, roots, auth_file, loading_required=False)
    require(Path(evidence_dir) == Path(roots["native_runtime"]).parent / "private/loading",
            "loading-unqualified: Loading qualification requires the reserved loading phase")
    root = Path(suite_root).resolve(strict=True)
    profile = parse_json(read_bytes(root, suite["host"]["isolation"]["profile"]))
    bindings = _isolation_bindings(suite, root, profile, layout)
    frozen = _immutable(suite, root, roots)
    discovery, entry_bytes = _discovery(runtime, roots), _entry_bytes(runtime, roots)
    with _reserved_attempt(suite, task_id, arm_id, roots, evidence_dir, auth_file=auth_file) as state:
        _control_record(state)
        _guard(state, suite, root, roots, frozen)
        _current_isolation(state, "isolation-before", suite, root, layout, roots, auth_file, bindings)
        nonce = uuid.uuid4().hex
        entry, action, prompt = smoke_commands(runtime, nonce, state["evidence"], suite["host"]["capabilities"]["subagents"])
        calls, error, in_flight = [], None, False
        try:
            stages = [("image", [PROBE_PYTHON, "-I", "-B", "-c", IMAGE_READBACK, runtime["host_executable"]]),
                      ("version", [runtime["host_executable"], "--version"]), ("discovery", discovery["argv"]),
                      ("smoke", runtime["launch"]["argv"])]
            for name, command in stages:
                _guard(state, suite, root, roots, frozen)
                if name == "smoke":
                    _make_controls(state, roots, nonce)
                in_flight = True
                call = _call(state, name, profile, layout, runtime, command, prompt=prompt if name == "smoke" else None)
                calls.append(call)
                in_flight = False
                require(call["lifecycle"]["status"] == "verified", "isolation-unqualified: Owned native cleanup was not verified")
                _guard(state, suite, root, roots, frozen)
                if name == "image":
                    image_observation(call["process"], runtime)
                elif name == "version":
                    version_observation(call["process"], runtime)
                elif name == "discovery":
                    discovery_observation(call["process"], discovery["stdout"])
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            error = exc
            _save_record(state, "execution-error.json", {"type": type(exc).__name__, "message": str(exc)})
        observations = _observations(state, roots, nonce, entry, action, calls, entry_bytes, suite["host"]["capabilities"]["subagents"])
        restoration = _restore(state, suite, root, roots, frozen,
                               cleanup_verified=not in_flight and all(call["lifecycle"]["status"] == "verified" for call in calls))
        if len(calls) < 3:
            raise ValueError(f"loading-unqualified: Native identity/discovery was not established: {error}") from error
        isolation_ok = False
        if restoration["status"] == "passed":
            try:
                _current_isolation(state, "isolation-after", suite, root, layout, roots, auth_file, bindings)
                _guard(state, suite, root, roots, frozen)
                isolation_ok = True
            except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                _save_record(state, "isolation-after-error.json", {"error": str(exc)})
        checks, _ = loading_checks(runtime, calls, observations, restoration, entry_bytes=entry_bytes,
                                  discovery_stdout=discovery["stdout"], isolation_ok=isolation_ok and not in_flight,
                                  subagents=suite["host"]["capabilities"]["subagents"])
        return _loading_receipt(state, suite, arm_id, runtime, profile, bindings, calls, nonce, checks)


def run_native_writer(suite, suite_root, task_id, arm_id, roots, evidence_dir, *, auth_file=None):
    """Ordinary writing never bootstraps missing native loading evidence."""
    if os.path.lexists(evidence_dir):
        raise ValueError(f"input-exists: {evidence_dir}")
    runtime, layout = _admit(suite, suite_root, task_id, arm_id, roots, auth_file, loading_required=True)
    require(Path(evidence_dir) == Path(roots["native_runtime"]).parent / "private/writer",
            "loading-unqualified: Ordinary writing requires the reserved writer phase")
    root = Path(suite_root).resolve(strict=True)
    profile = parse_json(read_bytes(root, suite["host"]["isolation"]["profile"]))
    bindings = _isolation_bindings(suite, root, profile, layout)
    control = Path(roots["native_runtime"]).parent / "private"
    reservation_raw = read_bytes(control, "reservation.json")
    verify_loading_bundle(suite, root, arm_id, runtime, layout, parse_json(reservation_raw), hashlib.sha256(reservation_raw).hexdigest(),
                          discovery=_discovery(runtime, roots), entry_bytes=_entry_bytes(runtime, roots), isolation_bindings=bindings)
    frozen = _immutable(suite, root, roots)
    with _reserved_attempt(suite, task_id, arm_id, roots, evidence_dir, auth_file=auth_file) as state:
        _control_record(state)
        _current_isolation(state, "isolation-before", suite, root, layout, roots, auth_file, bindings)
        for name, command in (("image", [PROBE_PYTHON, "-I", "-B", "-c", IMAGE_READBACK, runtime["host_executable"]]),
                              ("version", [runtime["host_executable"], "--version"])):
            _guard(state, suite, root, roots, frozen)
            call = _call(state, name, profile, layout, runtime, command)
            require(call["lifecycle"]["status"] == "verified", "isolation-unqualified: Native readback cleanup failed")
            (image_observation if name == "image" else version_observation)(call["process"], runtime)
        _guard(state, suite, root, roots, frozen)
        task = suite["tasks"][task_id]
        tokens = {"{workspace}": "/workspace", "{product}": "/product"}
        entry = read_bytes(root, suite["arms"][arm_id]["entry"]).decode("utf-8")
        materials = [re.sub(r"\{(?:workspace|product)\}", lambda match: tokens[match.group()], entry),
                     read_bytes(root, task["brief"]).decode("utf-8")]
        if task["clarifications"] is not None:
            materials.append(read_bytes(root, task["clarifications"]).decode("utf-8"))
        call = _call(state, "writer", profile, layout, runtime, runtime["launch"]["argv"], prompt="\n\n".join(materials))
        _guard(state, suite, root, roots, frozen)
        qualification = state["evidence"] / "isolation-before/qualification.json"
        timing = parse_json(read_bytes(qualification.parent, "timing.json"))
        seconds = timing["seconds"]
        require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0, "Invalid isolation timing")
        record = {"schema": "coding-trial-writer/v1", "suite_sha256": fingerprint(suite), "task": task_id, "arm": arm_id,
                  "budget": {"timeout_seconds": 900, "output_bytes": 8388608}, "command": call["argv"],
                  "environment": call["environment"], "process": call["process"], "telemetry": native_usage(call["process"]),
                  "isolation": {"adapter": "docker-v1", "status": "passed" if call["lifecycle"]["status"] == "verified" else "failed",
                                "qualification_seconds": seconds,
                                "qualification": {"path": str(qualification), "sha256": file_sha(qualification.parent, qualification.name)},
                                "container_id": call["container_id"], "lifecycle": call["lifecycle"]}}
        _save_record(state, "writer.json", record)
        return record
