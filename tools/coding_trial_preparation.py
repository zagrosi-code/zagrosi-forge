"""Owned offline native setup, initial reservations and loading evidence copies."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from coding_trial_docker import run_owned
from coding_trial_inventory import copy_snapshot, fingerprint, inventory, relative_path
from coding_trial_loading_evidence import IMAGE_READBACK, PROBE_PYTHON, complete, image_observation, loading_configuration
from coding_trial_manifest import validate_suite
from coding_trial_native import (
    FIXED_ENV, _package_identity, _preparation, _preparation_commands, _preparation_observation, _regular_bytes,
    native_recipe, read_native_runtime,
)
from coding_trial_qualification import _instant, fields, parse_json, read_bytes, require, sha
from coding_trial_reservation import MUTABLE, _anchor as _verify_anchor, _auth_identity, _git_entries

BOUNDS = {"timeout_seconds": 60, "output_bytes": 1048576}


def _reference(root, path):
    name = path.relative_to(root).as_posix()
    return {"path": name, "sha256": hashlib.sha256(_regular_bytes(root, name)).hexdigest()}


def _read_ref(root, reference):
    fields(reference, "path sha256", "Native evidence reference")
    relative_path(reference["path"])
    sha(reference["sha256"])
    raw = _regular_bytes(root, reference["path"])
    require(hashlib.sha256(raw).hexdigest() == reference["sha256"], "Native evidence bytes changed")
    return raw


def _save(root, path, value):
    with path.open("xb") as stream:
        stream.write((json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return _reference(root, path)


def _error(exc, path):
    code = str(exc).split(":", 1)[0]
    if code not in {"suite-invalid", "input-exists", "unsupported-profile", "loading-unqualified",
                    "isolation-unqualified", "input-drift", "execution-cancelled"}:
        code = "loading-unqualified"
    return {"code": code, "message": str(exc), "path": str(path)}


def _layout(trial, attempt, profile):
    setup = trial / "private/native-setup"
    mounts = [{"source": str(setup / name), "target": target, "read_only": False}
              for name, target in (("codex", "/codex"), ("home", "/home/forge"))]
    mounts.append({"source": str(trial / "native-runtime/marketplace"), "target": "/marketplace", "read_only": True})
    return {"role": "preparation", "task": attempt["identity"]["task"], "arm": attempt["identity"]["arm"],
            "mounts": mounts, "user": profile["user"], "network": "none"}


def _runtime(suite, root, arm_id, product):
    arm, host = suite["arms"][arm_id], suite["host"]
    installed = added = None
    if arm["product"] is not None:
        name, version = _package_identity(product)
        identifier = name + "@forge-evaluator"
        installed = {"plugin_id": identifier, "version": version,
                     "path": f"/codex/plugins/cache/forge-evaluator/{name}/{version}",
                     "inventory_sha256": arm["product"]["inventory_sha256"]}
        added = {"pluginId": identifier, "name": name, "marketplaceName": "forge-evaluator",
                 "version": version, "installedPath": installed["path"], "authPolicy": "ON_USE"}
    runtime = {"schema": "coding-trial-native-runtime/v1", "host_version": host["version"],
               "host_executable": host["executable"], "image_digest": host["isolation"]["image_digest"],
               "product_sha256": fingerprint(inventory(product)), "installed": installed,
               "selected_entry": arm["loading"].get("selected_entry"),
               "entry_sha256": hashlib.sha256(read_bytes(root, arm["entry"])).hexdigest(),
               "configuration_sha256": fingerprint(arm["configuration"]),
               "capabilities_sha256": fingerprint(host["capabilities"]),
               "profile_sha256": hashlib.sha256(read_bytes(root, host["isolation"]["profile"])).hexdigest()}
    runtime["launch"] = native_recipe(suite, arm_id, runtime)
    return runtime, added


def _raw_step(trial, folder, step):
    paths = sorted((path for path in folder.iterdir() if re.fullmatch(r"docker-[0-9]+\.json", path.name)),
                   key=lambda path: int(path.stem.removeprefix("docker-")))
    if os.path.lexists(folder / "container.cid"):
        paths.append(folder / "container.cid")
    step["evidence"] = [_reference(trial, path) for path in paths]
    if os.path.lexists(folder / "lifecycle.json"):
        step["lifecycle"] = _reference(trial, folder / "lifecycle.json")


def _setup_call(trial, index, profile, name, argv):
    number = len(index["steps"])
    folder = trial / "private/native-setup/steps" / f"{number:03d}"
    folder.mkdir()
    step = {"number": number, "id": name, "argv": argv, "cwd": "/", "prompt": None,
            "started_at": datetime.now(timezone.utc).isoformat(), "ended_at": None,
            "result": None, "lifecycle": None, "evidence": [], "error": None}
    index["steps"].append(step)
    try:
        result = run_owned(profile, index["layout"], argv, cwd="/", env=dict(FIXED_ENV), prompt=None,
                           timeout=BOUNDS["timeout_seconds"], output_limit=BOUNDS["output_bytes"], evidence_dir=folder)
        step["result"] = _save(trial, folder / "result.json", result)
        require(result["lifecycle"]["status"] == "verified", "isolation-unqualified: Native setup cleanup was not verified")
        require(complete(result["process"]), "loading-unqualified: Native setup command did not complete successfully")
        return result["process"]
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        step["error"] = _error(exc, folder)
        raise
    finally:
        step["ended_at"] = datetime.now(timezone.utc).isoformat()
        _raw_step(trial, folder, step)


def prepare_native(suite, suite_root, attempt, *, auth_file=None):
    """Create one runtime offline; return truthful refs even on ordinary failure."""
    root = Path(suite_root)
    trial = Path(attempt["roots"]["workspace"]).parent
    native, setup = trial / "native-runtime", trial / "private/native-setup"
    arm_id, task_id = attempt["identity"]["arm"], attempt["identity"]["task"]
    profile_raw = read_bytes(root, suite["host"]["isolation"]["profile"])
    profile = parse_json(profile_raw)
    result = {"runtime": None, "preparation": None, "setup": None, "error": None}
    index = {"schema": "coding-trial-native-setup/v1", "identity": deepcopy(attempt["identity"]),
             "profile_sha256": hashlib.sha256(profile_raw).hexdigest(), "layout": _layout(trial, attempt, profile),
             "environment": dict(FIXED_ENV), "bounds": dict(BOUNDS), "status": "failed", "steps": [], "error": None}
    setup.mkdir()
    (setup / "steps").mkdir()
    try:
        for path in (setup / "home", setup / "codex", native, native / "home", native / "codex",
                     native / "codex/plugins", native / "marketplace"):
            path.mkdir()
        if auth_file is not None:
            (native / "codex/auth.json").touch(exist_ok=False)
        product = trial / "product"
        runtime, added = _runtime(suite, root, arm_id, product)
        initial_product = inventory(product)
        if added is not None:
            copy_snapshot(product, native / "marketplace/payload", initial_product)
            catalog = native / "marketplace/.agents/plugins/marketplace.json"
            catalog.parent.mkdir(parents=True)
            _save(native, catalog, {"name": "forge-evaluator", "plugins": [{"name": added["name"],
                "source": {"source": "local", "path": "./payload"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"}, "category": "Productivity"}]})
        initial_marketplace = inventory(native / "marketplace")
        commands, observations = _preparation_commands(runtime, suite, arm_id, added)
        names = ["version", "discovery"] if added is None else ["version", "marketplace-add", "plugin-add", "discovery"]
        executions = []
        for name, argv, expected in zip(names, commands, observations):
            process = _setup_call(trial, index, profile, name, argv)
            execution = {"argv": argv, **{key: process[key] for key in ("returncode", "stdout", "stderr")}}
            _preparation_observation(execution, argv, expected, discovery=name == "discovery" and added is not None)
            executions.append(execution)
        preparation = {"schema": "coding-trial-native-preparation/v1", "image_digest": runtime["image_digest"],
                       "executions": executions}
        reference = _save(trial, trial / "private/native-preparation.json", preparation)
        runtime["preparation"] = {"path": "native-preparation.json", "sha256": reference["sha256"]}
        _preparation(runtime, suite, arm_id, trial / "private", added)
        result["preparation"] = reference
        process = _setup_call(trial, index, profile, "image",
                              [PROBE_PYTHON, "-I", "-B", "-c", IMAGE_READBACK, runtime["host_executable"]])
        image = parse_json(process["stdout"])
        runtime.update(host_executable_sha256=image["executable_sha256"], requirements_sha256=image["requirements_sha256"])
        image_observation(process, runtime)
        require(inventory(product) == initial_product and inventory(native / "marketplace") == initial_marketplace,
                "input-drift: Native setup changed its frozen product or catalog")
        if added is None:
            (native / "plugins").mkdir()
        else:
            cache = runtime["installed"]["path"].removeprefix("/codex/plugins/")
            source = setup / "codex/plugins"
            require(inventory(source / cache) == initial_product, "loading-unqualified: Installed cache differs from product")
            copy_snapshot(source, native / "plugins", inventory(source, included=(cache,)))
        runtime.update(plugins_sha256=fingerprint(inventory(native / "plugins")),
                       marketplace_sha256=fingerprint(initial_marketplace))
        reference = _save(trial, native / "runtime.json", runtime)
        read_native_runtime(suite, root, task_id, arm_id, attempt["roots"], auth_file=auth_file)
        result["runtime"] = reference
        index["status"] = "passed"
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        result["error"] = index["error"] = _error(exc, setup)
        if index["steps"] and index["steps"][-1]["error"] is None:
            index["steps"][-1]["error"] = dict(index["error"])
    result["setup"] = _save(trial, setup / "setup.json", index)
    return result


def _anchor(path):
    require(path.resolve(strict=True) == path, "Creation path was redirected")
    info, parent = path.stat(), path.parent.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode,
            "parent_device": parent.st_dev, "parent_inode": parent.st_ino}


def reserve_native(suite, study, attempt, *, auth_file=None):
    """Bind initial bytes only inside the caller's fresh creation chain."""
    trial = Path(attempt["roots"]["workspace"]).parent
    control, native = trial / "private", trial / "native-runtime"
    initial = control / "initial"
    initial.mkdir()
    mutable = {"workspace": trial / "workspace", "generated": trial / "generated",
               "home": native / "home", "codex": native / "codex"}
    snapshots = {}
    for name in (*MUTABLE, "workspace_git"):
        source = mutable["workspace"] / ".git" if name == "workspace_git" else mutable[name]
        entries = _git_entries(source) if name == "workspace_git" else inventory(source, excluded=(".git",) if name == "workspace" else ())
        destination = initial / name
        copy_snapshot(source, destination, entries)
        snapshots[name] = {"path": "initial/" + name, "inventory_sha256": fingerprint(entries), "anchor": _anchor(destination)}
    raw = _regular_bytes(trial, "trial.json")
    require(raw == _regular_bytes(control, "initial-attempt.json"), "Initial attempt bytes differ")
    anchored = {"attempt": trial, "private": control, "initial": initial, **mutable}
    reservation = {"schema": "coding-trial-native-reservation/v1", "loading_configuration_sha256": loading_configuration(suite),
        "suite_source_sha256": study["manifest"]["sha256"],
        "attempt_record": {"path": "../trial.json", "initial_path": "initial-attempt.json", "sha256": hashlib.sha256(raw).hexdigest()},
        "task": attempt["identity"]["task"], "arm": attempt["identity"]["arm"], "scheduled_position": attempt["scheduled_position"],
        "roots": deepcopy(attempt["roots"]), "runtime_sha256": attempt["native"]["runtime"]["sha256"],
        "auth": _auth_identity(auth_file), "anchors": {name: _anchor(path) for name, path in anchored.items()}, "snapshots": snapshots}
    return _save(trial, control / "reservation.json", reservation)


def package_loading(suite, suite_root, attempt, receipt):
    """Copy the successful bound closure; never alter its original raw records."""
    root = Path(suite_root)
    trial = Path(attempt["roots"]["workspace"]).parent
    source = trial / "private/loading"
    require(parse_json(_regular_bytes(source, "qualification.json")) == receipt,
            "loading-unqualified: Returned loading receipt differs from saved bytes")
    restoration = parse_json(_regular_bytes(source, "restoration.json"))
    require(receipt["checks"] and all(item["status"] == "passed" for item in receipt["checks"])
            and restoration["status"] == "passed", "loading-unqualified: Loading or restoration did not pass")
    base = root / "_attempts"
    if os.path.lexists(base):
        require(attempt["scheduled_position"] > 0 and base.resolve(strict=True) == base and base.is_dir(),
                "input-exists: Invalid or preexisting derived evidence namespace")
    else:
        base.mkdir()
    folder = base / str(attempt["scheduled_position"])
    folder.mkdir()
    destination = folder / "loading"
    names = tuple(item["path"] for item in receipt["evidence"])
    require(len(names) == len(set(names)), "loading-unqualified: Duplicate loading evidence")
    for reference in receipt["evidence"]:
        _read_ref(source, reference)
    copy_snapshot(source, destination, inventory(source, included=names))
    derived = deepcopy(receipt)
    prefix = destination.relative_to(root).as_posix() + "/"
    for reference in derived["evidence"]:
        reference["path"] = prefix + reference["path"]
    for check in derived["checks"]:
        check["evidence"] = [prefix + name for name in check["evidence"]]
    derived_ref = _save(root, destination / "receipt.json", derived)
    execution = deepcopy(suite)
    execution["arms"][attempt["identity"]["arm"]]["loading"]["receipt"] = derived_ref["path"]
    execution = validate_suite(execution, root)
    manifest_ref = _save(root, folder / "execution.json", execution)
    manifest_ref["suite_sha256"] = fingerprint(execution)
    return execution, {"loading_source": _reference(trial, source / "qualification.json"),
                       "loading_derived": derived_ref, "execution_manifest": manifest_ref,
                       "restoration": _reference(trial, source / "restoration.json")}


def verify_setup_evidence(trial, attempt, suite, suite_root):
    """Follow only the fixed setup index; never inspect installer scratch/auth."""
    trial, root = Path(trial), Path(suite_root)
    reference = attempt["native"]["setup"]
    require(reference["path"] == "private/native-setup/setup.json", "Unexpected setup evidence locator")
    index = parse_json(_read_ref(trial, reference))
    fields(index, "schema identity profile_sha256 layout environment bounds status steps error", "Native setup index")
    profile_raw = read_bytes(root, suite["host"]["isolation"]["profile"])
    profile = parse_json(profile_raw)
    require(index["schema"] == "coding-trial-native-setup/v1" and index["identity"] == attempt["identity"]
            and index["profile_sha256"] == hashlib.sha256(profile_raw).hexdigest()
            and index["layout"] == _layout(trial, attempt, profile) and index["environment"] == FIXED_ENV
            and index["bounds"] == BOUNDS and index["status"] == "passed" and index["error"] is None,
            "Native setup identity or outcome differs")
    runtime = parse_json(_read_ref(trial, attempt["native"]["runtime"]))
    preparation = parse_json(_read_ref(trial, attempt["native"]["preparation"]))
    require(attempt["native"]["runtime"]["path"] == "native-runtime/runtime.json"
            and attempt["native"]["preparation"]["path"] == "private/native-preparation.json"
            and runtime["preparation"] == {"path": "native-preparation.json", "sha256": attempt["native"]["preparation"]["sha256"]},
            "Native runtime preparation reference differs")
    names = (["version", "discovery"] if suite["arms"][attempt["identity"]["arm"]]["product"] is None
             else ["version", "marketplace-add", "plugin-add", "discovery"])
    commands = [entry["argv"] for entry in preparation["executions"]]
    commands.append([PROBE_PYTHON, "-I", "-B", "-c", IMAGE_READBACK, runtime["host_executable"]])
    require(type(index["steps"]) is list and len(index["steps"]) == len(names) + 1 == len(commands), "Incomplete native setup")
    for number, (step, name, argv) in enumerate(zip(index["steps"], [*names, "image"], commands)):
        fields(step, "number id argv cwd prompt started_at ended_at result lifecycle evidence error", "Native setup step")
        require(type(step["number"]) is int and step["number"] == number and step["id"] == name and step["argv"] == argv
                and step["cwd"] == "/" and step["prompt"] is None and step["error"] is None
                and _instant(step["started_at"]) <= _instant(step["ended_at"]), "Native setup invocation differs")
        prefix = f"private/native-setup/steps/{number:03d}/"
        require(step["result"]["path"] == prefix + "result.json" and step["lifecycle"]["path"] == prefix + "lifecycle.json",
                "Native setup result leaves its step")
        result = parse_json(_read_ref(trial, step["result"]))
        lifecycle = parse_json(_read_ref(trial, step["lifecycle"]))
        require(isinstance(result["container_id"], str) and re.fullmatch(r"[0-9a-f]{64}", result["container_id"]),
                "Invalid setup container ID")
        require(complete(result["process"]) and result["lifecycle"]["status"] == "verified", "Native setup was incomplete")
        fields(lifecycle, "container_id owner status reason evidence", "Native setup lifecycle")
        require(isinstance(lifecycle["owner"], str) and re.fullmatch(r"[0-9a-f]{32}", lifecycle["owner"])
                and lifecycle["container_id"] == result["container_id"]
                and all(lifecycle[key] == result["lifecycle"][key] for key in ("status", "reason", "evidence")),
                "Native setup raw lifecycle differs")
        raw = {}
        for item in step["evidence"]:
            name = item["path"]
            require(name.startswith(prefix) and (re.fullmatch(r"docker-[0-9]+\.json", name[len(prefix):])
                    or name == prefix + "container.cid") and name not in raw, "Unexpected raw setup evidence")
            raw[name] = _read_ref(trial, item)
        refs = lifecycle["evidence"]
        require(type(refs) is list and refs and len(refs) == len(set(refs)), "Missing native setup lifecycle evidence")
        for path in refs:
            require(Path(path).is_absolute() and str(trial / Path(path).relative_to(trial)) == path
                    and Path(path).relative_to(trial).as_posix() in raw, "Unbound setup lifecycle evidence")
        expected_start = [profile["docker_executable"], "--host", profile["endpoint"], "--config",
                          str(trial / prefix / "docker-config"), "start", "--attach", "--interactive", result["container_id"]]
        starts = [parse_json(raw[Path(path).relative_to(trial).as_posix()]) for path in refs]
        starts = [item for item in starts if item.get("argv") == expected_start]
        require(len(starts) == 1 and starts[0]["process"] == result["process"], "Setup process differs from raw Docker start")
        cid = raw.get(prefix + "container.cid")
        require(cid is not None and cid.decode("ascii").strip() == result["container_id"], "Setup container ID differs")
        if number < len(preparation["executions"]):
            observed = {"argv": argv, **{key: result["process"][key] for key in ("returncode", "stdout", "stderr")}}
            require(observed == preparation["executions"][number], "Native preparation summary differs from its raw result")
        else:
            image_observation(result["process"], runtime)


def _reservation_evidence(trial, attempt, suite, root, reservation):
    """Read retained creation evidence, never the current workspace or auth file."""
    fields(reservation, "schema loading_configuration_sha256 suite_source_sha256 attempt_record task arm scheduled_position "
           "roots runtime_sha256 auth anchors snapshots", "Native reservation")
    require(reservation["schema"] == "coding-trial-native-reservation/v1"
            and reservation["loading_configuration_sha256"] == loading_configuration(suite)
            and reservation["task"] == attempt["identity"]["task"]
            and reservation["arm"] == attempt["identity"]["arm"]
            and reservation["scheduled_position"] == attempt["scheduled_position"]
            and reservation["roots"] == attempt["roots"]
            and reservation["runtime_sha256"] == attempt["native"]["runtime"]["sha256"],
            "Native reservation identity differs")
    require(attempt["study"]["path"] == str(root.parent / "study.json"), "Native study location differs")
    study = parse_json(_read_ref(root.parent, {"path": "study.json", "sha256": attempt["study"]["sha256"]}))
    require(reservation["suite_source_sha256"] == study["manifest"]["sha256"], "Reserved suite source differs")
    initial = reservation["attempt_record"]
    fields(initial, "path initial_path sha256", "Initial attempt reference")
    require(initial["path"] == "../trial.json" and initial["initial_path"] == "initial-attempt.json",
            "Initial attempt location differs")
    original = parse_json(_read_ref(trial, {"path": "private/initial-attempt.json", "sha256": initial["sha256"]}))
    require(original["schema"] == attempt["schema"] and all(original[key] == attempt[key]
            for key in ("identity", "study", "scheduled_position", "budget", "qualify_loading", "roots", "initial_git")),
            "Initial native attempt identity differs")
    require(all(original["native"][name] == attempt["native"][name] for name in ("runtime", "preparation", "setup")),
            "Initial native setup references differ")
    fields(reservation["snapshots"], "workspace workspace_git home codex generated", "Initial native snapshots")
    for name, snapshot in reservation["snapshots"].items():
        if name == "workspace_git" and snapshot is None:
            continue
        fields(snapshot, "path inventory_sha256 anchor", "Initial native snapshot")
        require(snapshot["path"] == "initial/" + name, "Initial snapshot location differs")
        path = trial / "private" / snapshot["path"]
        _verify_anchor(path, snapshot["anchor"])
        entries = _git_entries(path) if name == "workspace_git" else inventory(path)
        require(fingerprint(entries) == snapshot["inventory_sha256"], "Initial native snapshot changed")
    require(reservation["snapshots"]["workspace"]["inventory_sha256"] == attempt["identity"]["baseline_sha256"],
            "Initial native snapshot differs from baseline")


def _loading_evidence(root, receipt):
    """Bind only the receipt's explicit finite evidence list."""
    require(receipt["schema"] == "coding-trial-qualification/v1" and receipt["kind"] == "loading",
            "Native loading receipt kind differs")
    require(type(receipt["evidence"]) is list and receipt["evidence"], "Missing native loading evidence")
    names = set()
    for reference in receipt["evidence"]:
        _read_ref(root, reference)
        require(reference["path"] not in names, "Duplicate native loading evidence")
        names.add(reference["path"])
    return names


def verify_native_evidence(trial, attempt, suite, suite_root):
    """Verify terminal native provenance without replaying admission or execution."""
    trial, root = Path(trial), Path(suite_root)
    native = attempt["native"]
    require(native is not None or suite["purpose"] != "prospective", "Missing native delivery references")
    if native is None:
        return
    locations = {"runtime": "native-runtime/runtime.json", "preparation": "private/native-preparation.json",
                 "setup": "private/native-setup/setup.json", "reservation": "private/reservation.json",
                 "loading_source": "private/loading/qualification.json", "restoration": "private/loading/restoration.json"}
    fields(native, " ".join([*locations, "loading_derived", "execution_manifest"]), "Native attempt references")
    records = {}
    for name, path in locations.items():
        if native[name] is not None:
            require(native[name]["path"] == path, "Unexpected native " + name + " location")
            records[name] = parse_json(_read_ref(trial, native[name]))
    delivered = attempt["runner"] is not None
    if delivered:
        require({"runtime", "preparation", "setup", "reservation"} <= records.keys(), "Incomplete native delivery references")
        verify_setup_evidence(trial, attempt, suite, root)
    if "reservation" in records:
        _reservation_evidence(trial, attempt, suite, root, records["reservation"])
    if "loading_source" in records:
        _loading_evidence(trial / "private/loading", records["loading_source"])
    derived, manifest = native["loading_derived"], native["execution_manifest"]
    require((derived is None) == (manifest is None), "Incomplete derived loading references")
    if delivered and attempt["qualify_loading"]:
        require(derived is not None and {"loading_source", "restoration"} <= records.keys(),
                "Missing terminal loading qualification")
    execution = suite
    if derived is not None:
        require(attempt["qualify_loading"] and {"loading_source", "restoration"} <= records.keys(),
                "Derived loading has no original qualification")
        prefix = f"_attempts/{attempt['scheduled_position']}/"
        require(derived["path"] == prefix + "loading/receipt.json", "Unexpected derived loading location")
        proof = parse_json(_read_ref(root, derived))
        _loading_evidence(root, proof)
        expected = deepcopy(records["loading_source"])
        for reference in expected["evidence"]:
            reference["path"] = prefix + "loading/" + reference["path"]
        for check in expected["checks"]:
            check["evidence"] = [prefix + "loading/" + name for name in check["evidence"]]
        require(proof == expected, "Derived loading receipt differs from original evidence")
        fields(manifest, "path sha256 suite_sha256", "Native execution manifest reference")
        require(manifest["path"] == prefix + "execution.json", "Unexpected execution manifest location")
        execution = parse_json(_read_ref(root, {key: manifest[key] for key in ("path", "sha256")}))
        expected = deepcopy(suite)
        expected["arms"][attempt["identity"]["arm"]]["loading"]["receipt"] = derived["path"]
        require(execution == expected and fingerprint(execution) == manifest["suite_sha256"],
                "Native execution manifest differs from its selected loading proof")
    if delivered:
        writer = parse_json(_read_ref(trial, attempt["runner"]))
        require(writer["schema"] == "coding-trial-writer/v1" and writer["suite_sha256"] == fingerprint(execution)
                and writer["task"] == attempt["identity"]["task"] and writer["arm"] == attempt["identity"]["arm"],
                "Native writer execution identity differs")
