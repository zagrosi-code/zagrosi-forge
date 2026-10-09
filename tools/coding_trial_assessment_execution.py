"""Owned assessment commands and the private oracle/worker exchange."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from threading import Event, Thread

import coding_trial_isolation as isolation
import coding_trial_process as process
from coding_trial_inventory import copy_snapshot, inventory
from coding_trial_observation_client import SCHEMA, _directory, _read, _request, _response
from coding_trial_qualification import fields, parse_json, require

IMAGE_PYTHON = "/usr/local/bin/python3"
ERRORS = {"input-drift", "candidate-drift", "receipt-invalid", "runner-failed",
          "unsupported-profile", "isolation-unqualified", "execution-cancelled"}


def process_passed(value):
    return (type(value) is dict and type(value.get("returncode")) is int and value["returncode"] == 0
            and all(value.get(key) is False for key in ("timed_out", "stdout_truncated", "stderr_truncated"))
            and not value.get("cancelled") and not value.get("termination_error")
            and isinstance(value.get("stdout"), str) and isinstance(value.get("stderr"), str))


def _error(exc, default="receipt-invalid"):
    code = str(exc).split(":", 1)[0]
    return {"code": code if code in ERRORS else default, "message": str(exc), "path": None}


def _reference(trial, path, *, limit=None):
    return _bytes_reference(trial, path, _read(path, limit))


def _bytes_reference(trial, path, raw):
    return {"path": path.relative_to(trial).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}


def _write(path, raw):
    """Publish exclusively; keep POSIX writes attached to the checked parent."""
    anchor = _directory(path.parent)
    parent = None
    try:
        if os.name == "posix":
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(parent)
            require((info.st_dev, info.st_ino, info.st_mode) == anchor, "Evidence directory changed")
        handle = os.open(path.name if parent is not None else path,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600, dir_fd=parent)
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
        _directory(path.parent, anchor)
    finally:
        if parent is not None:
            os.close(parent)


def _save(trial, path, value):
    _write(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return _reference(trial, path)


def _root_paths(suite_root, trial, attempt, evidence_dir, role, number):
    folder = trial / "assessment-runtime" / evidence_dir.name / f"{role}-{number:06d}"
    return {"workspace": str(trial / attempt["candidate"]["path"]),
            "product": str(trial / "product"), "public": str(folder / "public"),
            "generated": str(folder / "generated"), "native_runtime": None,
            "private": [str(suite_root.parent), str(trial / "private"),
                        str(trial / "assessor/assessments"), str(trial / "assessor/git"),
                        str(trial / "assessor/workflow"), str(trial / "workspace")]}


def _prepare_roots(suite, suite_root, trial, attempt, evidence_dir, role, number):
    roots = _root_paths(suite_root, trial, attempt, evidence_dir, role, number)
    public = Path(roots["public"])
    public.parent.mkdir(parents=True)
    checks = suite["tasks"][attempt["identity"]["task"]]["checks"]
    commands = checks["native"] if role == "native" else [checks["worker"]]
    resources = tuple(sorted({name for command in commands
                              for name in [command["entry"], *command["support"]] if name is not None}))
    copy_snapshot(suite_root, public, inventory(suite_root, included=resources))
    generated = Path(roots["generated"])
    generated.mkdir()
    for name in suite["tasks"][attempt["identity"]["task"]]["scope"]["generated"]:
        (generated / name).mkdir(parents=True, exist_ok=True)
    return roots


def prepare_worker_roots(suite, suite_root, trial, attempt, evidence_dir):
    """Prepare the first descriptor-bound worker projection, never its execution."""
    return _prepare_roots(suite, Path(suite_root), Path(trial), attempt, Path(evidence_dir), "worker", 1)


def _command(command, roots, *, oracle=None):
    private = oracle is not None
    workspace = roots["workspace"] if private else "/workspace"
    entry = command["entry"]
    tokens = {"{workspace}": workspace, "{python}": oracle["python"] if private else IMAGE_PYTHON}
    if entry is not None:
        tokens["{entry}"] = str(Path(oracle["root"]) / entry) if private else str(PurePosixPath("/checks") / entry)
    if private:
        tokens.update({"{assessor}": str(oracle["root"]), "{receipt}": str(oracle["receipt"]),
                       "{assessment}": str(oracle["assessment"])})
    def expand(value):
        if value in {"{workspace}", "{entry}", "{assessor}", "{receipt}", "{assessment}", "{python}"}:
            require(value in tokens, f"unsupported-profile: Token unavailable in this role: {value}")
        return tokens.get(value, value)
    argv = [expand(value) for value in command["argv"]]
    env = {key: expand(value) for key, value in command["env"].items()}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="0")
    if private:
        executable = Path(argv[0]).resolve(strict=True)
        require(str(executable) == oracle["python"], "unsupported-profile: Oracle must use the frozen controller")
        argv[1:1] = ["-I", "-B"]
        env.pop("PYTHONPATH", None)
        cwd = Path(oracle["root"]) / command["cwd"]
        require(cwd.resolve(strict=True) == cwd and cwd.is_dir(), "unsupported-profile: Oracle cwd is redirected")
    else:
        cwd = PurePosixPath("/workspace") / command["cwd"]
    return argv, str(cwd), env


def _isolated_record(trial, folder, identity, command, argv, cwd, env, result):
    """Keep execution results while giving their private references one root."""
    envelope = dict(result["isolation"])
    qualification = envelope["qualification"]
    observed = _reference(trial, Path(qualification["path"]))
    require(observed["sha256"] == qualification["sha256"], "receipt-invalid: Qualification bytes changed")
    envelope["qualification"] = observed
    lifecycle = dict(envelope["lifecycle"])
    lifecycle["evidence"] = [_reference(trial, Path(path)) for path in lifecycle["evidence"]]
    envelope["lifecycle"] = lifecycle
    reference = _save(trial, folder / "result.json", {
        "schema": "coding-trial-check-execution/v1", "identity": identity,
        "command": command, "argv": argv, "cwd": cwd, "env": env,
        "bounds": {"timeout_seconds": command["timeout_seconds"], "output_bytes": command["output_bytes"]},
        "process": result["process"], "isolation": envelope})
    return reference, [reference, observed, *lifecycle["evidence"]]


def _isolated(suite, suite_root, trial, descriptor, command, roots, role, folder, *, prompt=None,
              cancel_event=None):
    identity = descriptor["identity"]
    layout = isolation.derive_layout(suite, suite_root, identity["task"], identity["arm"], roots, role=role)
    argv, cwd, env = _command(command, roots)
    folder.mkdir()
    result = isolation.execute_isolated(suite, suite_root, layout, argv, roots=roots, cwd=cwd, env=env,
        prompt=prompt, timeout=command["timeout_seconds"], output_limit=command["output_bytes"],
        evidence_dir=folder / "execution", cancel_event=cancel_event)
    reference, references = _isolated_record(trial, folder, identity, command, argv, cwd, env, result)
    passed = (process_passed(result["process"]) and result["isolation"]["status"] == "passed"
              and result["isolation"]["lifecycle"]["status"] == "verified")
    return result, reference, passed, references


def _descriptor(suite, suite_root, trial, path):
    descriptor = parse_json(_read(path))
    fields(descriptor, "schema identity suite_root manifest input_inventory_sha256 evaluator_sha256 "
           "controller observation_client candidate worker evidence_dir", "Assessment descriptor")
    attempt = parse_json(_read(trial / "trial.json"))
    study_path = Path(attempt["study"]["path"])
    raw = _read(study_path)
    require(hashlib.sha256(raw).hexdigest() == attempt["study"]["sha256"], "input-drift: Study changed")
    study = parse_json(raw)
    require(descriptor["schema"] == "coding-trial-assessment-input/v1"
            and descriptor["identity"] == attempt["identity"] and descriptor["suite_root"] == str(suite_root)
            and descriptor["evidence_dir"] == str(path.parent), "receipt-invalid: Descriptor identity differs")
    for name in ("manifest", "input_inventory_sha256", "evaluator_sha256", "controller"):
        require(descriptor[name] == study[name], f"input-drift: Descriptor {name} differs")
    candidate = {name: attempt["candidate"][name] for name in ("full_inventory_sha256", "assessed_sha256")}
    candidate["path"] = str(trial / attempt["candidate"]["path"])
    require(descriptor["candidate"] == candidate, "candidate-drift: Descriptor candidate differs")
    client = Path(study["evaluator"]["root"]) / "tools/coding_trial_observation_client.py"
    require(descriptor["observation_client"] == {"path": str(client),
            "sha256": hashlib.sha256(_read(client)).hexdigest()}, "input-drift: Oracle client differs")
    controller = descriptor["controller"]
    require(hashlib.sha256(_read(Path(controller["executable"]))).hexdigest() == controller["executable_sha256"],
            "input-drift: Oracle interpreter differs")
    task = suite["tasks"][attempt["identity"]["task"]]
    require(descriptor["worker"] == {"command": task["checks"]["worker"], "protocol": SCHEMA,
            "roots": _root_paths(suite_root, trial, attempt, path.parent, "worker", 1)},
            "receipt-invalid: Frozen worker descriptor differs")
    return descriptor, attempt, task


def _mailbox(mailbox, anchor, completed, number):
    _directory(mailbox, anchor)
    names = {path.name for path in mailbox.iterdir()}
    expected = {f"{index:06d}" for index in range(1, number + 1)} | {"client.lock"}
    require(names <= expected, "receipt-invalid: Unexpected observation sequence")
    for path, identity in completed:
        _directory(path, identity)
    current = mailbox / f"{number:06d}"
    if not os.path.lexists(current):
        return None
    _directory(current)
    return current if os.path.lexists(current / "request.ready") else None


def _verify_references(trial, references):
    for reference in references:
        require(_reference(trial, trial / reference["path"]) == reference,
                "receipt-invalid: Retained observation evidence changed")


def _oracle(suite, suite_root, trial, descriptor_path, descriptor, attempt, task, verify_inputs, output):
    folder = descriptor_path.parent
    command = task["checks"]["oracle"]
    receipt = folder / "oracle-receipt.json"
    argv, cwd, env = _command(command, descriptor["worker"]["roots"], oracle={
        "python": descriptor["controller"]["executable"], "root": suite_root,
        "receipt": receipt, "assessment": descriptor_path})
    mailbox = folder / "oracle-ipc"
    mailbox.mkdir(mode=0o700)
    anchor = _directory(mailbox)
    oracle_cancel, worker_cancel, done = Event(), Event(), Event()
    outcome = {}
    def invoke():
        try:
            outcome["process"] = process.execute(argv, Path(cwd), prompt=None,
                timeout=command["timeout_seconds"], output_limit=command["output_bytes"],
                env=env, inherit_env=False, cancel_event=oracle_cancel)
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            worker_cancel.set()
            done.set()
    thread = Thread(target=invoke, daemon=False)
    references, completed, seen = [_reference(trial, descriptor_path)], [], set()
    number = 1
    interrupted = None
    verify_inputs()
    thread.start()
    try:
        while not done.is_set():
            current = _mailbox(mailbox, anchor, completed, number)
            if current is None:
                done.wait(.01)
                continue
            directory_identity = _directory(current)
            observation = {"schema": "coding-trial-observation-result/v1", "identity": descriptor["identity"],
                           "number": number, "request": None, "execution": None, "response": None, "errors": []}
            try:
                require(_read(current / "request.ready", 0, parent_identity=directory_identity) == b"",
                        "receipt-invalid: Request marker is not empty")
                worker = task["checks"]["worker"]
                raw = _read(current / "request.json", worker["output_bytes"], parent_identity=directory_identity)
                value = parse_json(raw)
                _request(value)
                require(raw.endswith(b"\n"), "receipt-invalid: Request must end with a newline")
                require(value["id"] not in seen, "receipt-invalid: Duplicate observation ID")
                seen.add(value["id"])
                observation["request"] = _bytes_reference(trial, current / "request.json", raw)
                references.extend([observation["request"], _bytes_reference(trial, current / "request.ready", b"")])
                roots = (descriptor["worker"]["roots"] if number == 1 else
                         _prepare_roots(suite, suite_root, trial, attempt, folder, "worker", number))
                verify_inputs()
                result, observation["execution"], passed, execution_refs = _isolated(suite, suite_root, trial, descriptor,
                    worker, roots, "worker", folder / f"worker-{number:06d}",
                    prompt=raw.decode("utf-8"), cancel_event=worker_cancel)
                references.extend(execution_refs)
                verify_inputs()
                require(passed, "runner-failed: Worker process or cleanup failed")
                response_raw = result["process"]["stdout"].encode("utf-8")
                require(len(response_raw) <= worker["output_bytes"], "receipt-invalid: Worker response exceeds its bound")
                _response(parse_json(response_raw), value["id"])
                _directory(current, directory_identity)
                require(not done.is_set(), "runner-failed: Oracle exited with an outstanding request")
                _write(current / "response.json", response_raw)
                _write(current / "response.ready", b"")
                observation["response"] = _bytes_reference(trial, current / "response.json", response_raw)
                references.extend([observation["response"], _bytes_reference(trial, current / "response.ready", b"")])
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                observation["errors"].append(_error(exc))
                output["errors"].extend(observation["errors"])
                oracle_cancel.set()
                worker_cancel.set()
            reference = _save(trial, folder / f"observation-{number:06d}.json", observation)
            output["observations"].append(reference)
            references.append(reference)
            completed.append((current, directory_identity))
            number += 1
            if observation["errors"]:
                break
        _mailbox(mailbox, anchor, completed, number)
        require(not os.path.lexists(mailbox / f"{number:06d}"),
                "receipt-invalid: Oracle left an unhandled request")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        output["errors"].append(_error(exc))
    except BaseException as exc:
        interrupted = exc
        output["errors"].append(_error(exc, "runner-failed"))
    finally:
        oracle_cancel.set()
        worker_cancel.set()
        thread.join()
    if "error" in outcome:
        output["errors"].append(_error(outcome["error"], "runner-failed"))
    if "process" in outcome:
        output["oracle"]["process"] = _save(trial, folder / "oracle-process.json", {
            "schema": "coding-trial-oracle-execution/v1", "identity": descriptor["identity"],
            "command": command, "argv": argv, "cwd": cwd, "env": env, "process": outcome["process"]})
        if not process_passed(outcome["process"]):
            output["errors"].append(_error("runner-failed: Oracle did not complete successfully"))
    try:
        _verify_references(trial, references)
        verify_inputs()
        raw = _read(receipt, command["output_bytes"])
        parse_json(raw)
        output["oracle"]["receipt"] = _bytes_reference(trial, receipt, raw)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        output["errors"].append(_error(exc))
    if interrupted is not None:
        raise interrupted


def run_checks(suite, suite_root, trial, descriptor_path, *, verify_inputs):
    """Run frozen native checks and supervise one trusted private oracle."""
    suite_root, trial, descriptor_path = Path(suite_root), Path(trial), Path(descriptor_path)
    verify_inputs()
    descriptor, attempt, task = _descriptor(suite, suite_root, trial, descriptor_path)
    require(suite["host"]["isolation"] is not None, "unsupported-profile: Assessment requires Docker isolation")
    output = {"native": [], "oracle": {"process": None, "receipt": None}, "observations": [], "errors": []}
    references = []
    try:
        for number, command in enumerate(task["checks"]["native"], 1):
            verify_inputs()
            roots = _prepare_roots(suite, suite_root, trial, attempt, descriptor_path.parent, "native", number)
            result, reference, passed, execution_refs = _isolated(suite, suite_root, trial, descriptor, command, roots,
                "native", descriptor_path.parent / f"native-{number:06d}")
            output["native"].append({"id": command["id"], "record": reference})
            references.extend(execution_refs)
            verify_inputs()
            if not passed:
                output["errors"].append(_error(f"runner-failed: Native check {command['id']} or cleanup failed"))
        _oracle(suite, suite_root, trial, descriptor_path, descriptor, attempt, task, verify_inputs, output)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        if str(exc).startswith("unsupported-profile:"):
            raise
        output["errors"].append(_error(exc))
    finally:
        try:
            _verify_references(trial, references)
            verify_inputs()
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            output["errors"].append(_error(exc, "input-drift"))
    return output
