"""Strict suite resources and source-bound receipts; never live host admission."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from coding_trial_inventory import fingerprint, inventory, project, relative_path

def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, names, where):
    require(type(value) is dict, f"{where} must be an object")
    expected = set(names.split())
    require(set(value) == expected, f"{where} fields must be {', '.join(sorted(expected))}")


def text(value, where, *, empty=False):
    require(isinstance(value, str) and "\0" not in value and (empty or value.strip()),
             f"{where} must be a {'nonempty ' if not empty else ''}string without NUL")


def choice(value, choices, where):
    text(value, where)
    require(value in choices.split(), f"Unsupported {where}: {value}")


def integer(value, where, *, minimum=None):
    require(type(value) is int and (minimum is None or value >= minimum),
             f"Invalid integer for {where}")


def positive(value, where):
    require(type(value) in (int, float) and math.isfinite(value) and value > 0,
             f"{where} must be finite and positive")


def boolean(value, where):
    require(type(value) is bool, f"{where} must be a boolean")


def strings(value, where, *, nonempty=False, unique=False):
    require(type(value) is list and (value or not nonempty), f"Invalid list for {where}")
    for item in value:
        text(item, where)
    require(not unique or len(value) == len(set(value)), f"Duplicate {where}")


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "Invalid SHA256")


def image(value):
    require(isinstance(value, str) and re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", value),
             "Image must be pinned by SHA256")


def environment(value, where):
    require(type(value) is dict, f"{where} must be an object")
    for name, content in value.items():
        text(name, where)
        require("=" not in name, f"Invalid environment name: {name}")
        text(content, where, empty=True)
    require(value.get("PYTHONOPTIMIZE", "0") == "0", "PYTHONOPTIMIZE is harness-owned")


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=_json_object,
                      parse_constant=lambda value: (require(False, f"Invalid JSON number: {value}")))


def resource(root, name, *, directory=False):
    relative_path(name)
    try:
        path = (root / name).resolve(strict=True)
    except RuntimeError as exc:
        raise ValueError(f"Cyclic resource path: {name}") from exc
    require(path.is_relative_to(root), f"Resource escapes suite: {name}")
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
             f"Wrong resource type: {name}")
    require(directory or info.st_nlink == 1, f"Hard-linked resource: {name}")
    return path


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_bytes(root, name):
    path = resource(root, name)
    before = path.stat()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as handle:
        observed = os.fstat(handle.fileno())
        require(stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1,
                f"Resource is no longer a regular single-link file: {name}")
        require(_identity(observed) == _identity(before), f"Resource changed before reading: {name}")
        raw = handle.read()
        after = os.fstat(handle.fileno())
    require(_identity(after) == _identity(before) == _identity(path.lstat()),
            f"Resource changed during reading: {name}")
    return raw


def file_sha(root, name):
    return hashlib.sha256(read_bytes(root, name)).hexdigest()


CHECKS = {
    "environment": {"runtime-identity", "locked-dependencies", "native-baseline"},
    "task": {"contract-review", "baseline-preservation", "intended-feature-failures",
             "known-correct", "alternative-legal", "negative-controls"},
    "isolation": {"task-read-write", "product-read-only", "private-read-denied", "private-write-denied",
                  "relative-symlink-denied", "child-private-denied", "detached-child-stopped", "owned-container-removed"},
    "loading": {"native-discovery", "entry-routing", "required-tools", "task-read-write",
                "private-access-denied", "termination"},
}

def _private_resource(root, name, exposed):
    path = resource(root, name)
    require(not any(path.is_relative_to(folder) for folder in exposed),
            f"Private evaluator resource is exposed to a candidate: {name}")
    return path

def _instant(value):
    text(value, "execution instant")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset() == timezone.utc.utcoffset(result), "Execution time must be UTC")
    return result

def _receipt(root, name, kind, purpose, exposed, *, extra_checks=()):
    """Check recorded evidence syntax/content, without trusting it as live execution."""
    _private_resource(root, name, exposed)
    proof = parse_json(read_bytes(root, name))
    fields(proof, "schema kind bindings checks producer execution evidence", "qualification")
    require(proof["schema"] == "coding-trial-qualification/v1" and proof["kind"] == kind,
            f"Wrong qualification schema or kind: {name}")
    producer = proof["producer"]
    fields(producer, "name independent purpose", "producer")
    text(producer["name"], "producer name")
    boolean(producer["independent"], "producer independence")
    require(kind != "task" or producer["independent"], "Task admission requires independent review")
    require(producer["purpose"] == ("actual" if purpose == "prospective" else "synthetic"),
            "Qualification purpose differs from the suite")
    execution = proof["execution"]
    fields(execution, "argv executable_sha256 version platform started_at ended_at returncode", "execution")
    strings(execution["argv"], "execution argv", nonempty=True)
    sha(execution["executable_sha256"])
    for key in ("version", "platform"):
        text(execution[key], key)
    integer(execution["returncode"], "returncode")
    require(execution["returncode"] == 0, "Qualification execution failed")
    require(_instant(execution["started_at"]) <= _instant(execution["ended_at"]), "Execution times are reversed")
    require(type(proof["evidence"]) is list and proof["evidence"], "Qualification needs raw evidence")
    evidence = {}
    for item in proof["evidence"]:
        fields(item, "path sha256", "evidence")
        _private_resource(root, item["path"], exposed)
        require(item["path"] not in evidence, "Duplicate evidence resource")
        raw = read_bytes(root, item["path"])
        sha(item["sha256"])
        require(raw and hashlib.sha256(raw).hexdigest() == item["sha256"], "Empty or changed raw evidence")
        evidence[item["path"]] = raw
    expected = CHECKS[kind] | set(extra_checks)
    require(type(proof["checks"]) is list, "Qualification checks must be a list")
    observed = set()
    for check in proof["checks"]:
        fields(check, "id status evidence", "qualification check")
        text(check["id"], "check id")
        require(check["id"] in expected - observed, "Unexpected or duplicate qualification check")
        observed.add(check["id"])
        require(check["status"] == "passed", "Qualification check did not pass")
        strings(check["evidence"], "check evidence", nonempty=True, unique=True)
        require(set(check["evidence"]) <= evidence.keys(), "Check references unbound evidence")
    require(observed == expected, "Missing qualification checks")
    return proof, evidence

def _object_with_digest(evidence, digest):
    sha(digest)
    matches = []
    for raw in evidence.values():
        try:
            value = parse_json(raw)
            if type(value) is dict and fingerprint(value) == digest:
                matches.append(value)
        except (ValueError, UnicodeError):
            continue  # Process logs are raw evidence, not JSON objects.
    require(len(matches) == 1, "Canonical qualification object is missing or ambiguous")
    return matches[0]

def _runtime(value):
    fields(value, "executable_sha256 version platform image_digest public_environment dependencies", "runtime")
    sha(value["executable_sha256"])
    text(value["version"], "runtime version")
    text(value["platform"], "runtime platform")
    if value["image_digest"] is not None:
        image(value["image_digest"])
    environment(value["public_environment"], "public environment")
    require(type(value["dependencies"]) is dict, "Runtime dependencies must be an object")
    for name, dependency in value["dependencies"].items():
        text(name, "dependency name")
        fields(dependency, "version inventory_sha256", "runtime dependency")
        text(dependency["version"], "dependency version")
        sha(dependency["inventory_sha256"])


def _bindings(proof, expected, *, hashes=(), identifiers=()):
    """Compare observable declarations; runtime-only identities remain syntax checks."""
    value = proof["bindings"]
    fields(value, " ".join([*expected, *hashes, *identifiers]), f"{proof['kind']} bindings")
    for key, current in expected.items():
        require(value[key] == current, f"Stale {proof['kind']} binding: {key}")
    for key in hashes:
        sha(value[key])
    for key in identifiers:
        text(value[key], key)
    return value


def _preparation(task):
    value = task["source"]["preparation"]
    return None if value is None else fingerprint(value)


def _environment_proof(suite, root, task, baseline, exposed):
    reference = task["dependencies"]["environment"]
    if reference is None:
        return
    proof, evidence = _receipt(root, reference, "environment", suite["purpose"], exposed)
    isolation = suite["host"]["isolation"]
    image_digest = None if isolation is None else isolation["image_digest"]
    require(image_digest is not None or suite["purpose"] == "synthetic",
            "Prospective environment proof requires its prepared image")
    dependencies = project(baseline, tuple(task["dependencies"]["locks"] + task["scope"]["config"]))
    bindings = _bindings(proof, {
        "image_digest": image_digest,
        "dependency_sha256": fingerprint(dependencies),
        "preparation_sha256": _preparation(task),
    }, hashes=("runtime_sha256",))
    runtime = _object_with_digest(evidence, bindings["runtime_sha256"])
    _runtime(runtime)
    require(runtime["image_digest"] == image_digest, "Runtime image differs from the host")
    require(runtime["public_environment"] == suite["host"]["environment"],
            "Runtime public environment differs from the host")
    require(_object_with_digest(evidence, bindings["dependency_sha256"]) == dependencies,
            "Recorded dependencies differ from current lock/config files")


def _task_proof(suite, root, task, baseline, exposed):
    if task["admission"] is None:
        return
    proof, _ = _receipt(root, task["admission"], "task", suite["purpose"], exposed)
    environment_reference = task["dependencies"]["environment"]
    require(environment_reference is not None, "Task proof requires an environment proof")
    checks = task["checks"]
    commands = [*checks["native"], checks["oracle"], checks["worker"]]
    resources = sorted({name for command in commands
                        for name in [command["entry"], *command["support"]] if name is not None})
    _bindings(proof, {
        "source_sha256": fingerprint(baseline),
        "preparation_sha256": _preparation(task),
        "baseline_sha256": fingerprint(baseline),
        "brief_sha256": file_sha(root, task["brief"]),
        "clarifications_sha256": (None if task["clarifications"] is None
                                  else file_sha(root, task["clarifications"])),
        "check_ids_sha256": fingerprint({key: checks[key] for key in ("feature_ids", "preservation_ids")}),
        "checks_sha256": fingerprint(inventory(root, included=tuple(resources))),
        "environment_receipt_sha256": file_sha(root, environment_reference),
        "task_policy_sha256": fingerprint({key: value for key, value in task.items() if key != "admission"}),
    })


def _isolation_proof(suite, root, profile, exposed):
    isolation = suite["host"]["isolation"]
    if isolation is None or isolation["probe_receipt"] is None:
        return
    proof, _ = _receipt(root, isolation["probe_receipt"], "isolation", suite["purpose"], exposed,
                        extra_checks=profile["network_checks"])
    expected = {key: profile[key] for key in
                ("context", "endpoint", "server_id", "server_version", "image_digest", "platform", "network")}
    expected.update(profile_sha256=file_sha(root, isolation["profile"]),
                    limits_sha256=fingerprint(profile["limits"]))
    _bindings(proof, expected,
              hashes=("docker_executable_sha256", "adapter_sha256", "probe_sha256", "layout_sha256"),
              identifiers=("docker_version",))


def _loading_proof(suite, root, arm, profile, exposed):
    reference = arm["loading"]["receipt"]
    if reference is None:
        return
    require(profile is not None, "Loading proof requires its isolation profile")
    host = suite["host"]
    proof, _ = _receipt(root, reference, "loading", suite["purpose"], exposed,
                        extra_checks=("subagent-execution",) if host["capabilities"]["subagents"] else ())
    product = arm["product"]
    _bindings(proof, {
        "image_digest": profile["image_digest"],
        "host_version": host["version"],
        "model": host["model"],
        "effort": host["effort"],
        "product_sha256": fingerprint({}) if product is None else product["inventory_sha256"],
        "entry_sha256": file_sha(root, arm["entry"]),
        "configuration_sha256": fingerprint(arm["configuration"]),
        "capabilities_sha256": fingerprint(host["capabilities"]),
        "profile_sha256": file_sha(root, host["isolation"]["profile"]),
    }, hashes=("host_executable_sha256",))


def validate_qualifications(suite, root, baselines, profile):
    """Verify supplied receipt data only; execution must establish live admission."""
    exposed = [folder for folder, _ in baselines.values()]
    exposed.extend(resource(root, arm["product"]["payload"], directory=True)
                   for arm in suite["arms"].values() if arm["product"] is not None)
    public = {arm["entry"] for arm in suite["arms"].values()}
    for task in suite["tasks"].values():
        public.update(name for name in (task["brief"], task["clarifications"]) if name is not None)
        for command in [*task["checks"]["native"], task["checks"]["worker"]]:
            public.update(name for name in [command["entry"], *command["support"]] if name is not None)
    exposed.extend(resource(root, name) for name in sorted(public))
    for name, task in suite["tasks"].items():
        oracle = task["checks"]["oracle"]
        for reference in [oracle["entry"], *oracle["support"]]:
            _private_resource(root, reference, exposed)
        baseline = baselines[name][1]
        _environment_proof(suite, root, task, baseline, exposed)
        _task_proof(suite, root, task, baseline, exposed)
    _isolation_proof(suite, root, profile, exposed)
    for arm in suite["arms"].values():
        _loading_proof(suite, root, arm, profile, exposed)
