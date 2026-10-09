"""Private Docker boundary: pinned preflight and exact-ID owned lifecycle."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from coding_trial_process import execute
from coding_trial_qualification import parse_json, require

PROBE_PYTHON = "/usr/local/bin/python3"
LAUNCHER = "import json,os,sys; s=json.loads(sys.argv[1]); os.chdir(s['cwd']); os.execvpe(s['argv'][0],s['argv'],s['env'])"
LABEL = "org.zagrosi.forge.evaluator-owner"


def save_json(path, value):
    """Controller records never replace process-created files or links."""
    try:
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {path}") from exc


def invoke(state, args, *, prompt=None, timeout=30, output_limit=8388608, context=False):
    command = [state["profile"]["docker_executable"]]
    if not context:
        command += ["--host", state["profile"]["endpoint"], "--config", str(state["config"])]
    command += args
    environment = {"HOME": str(Path.home())} if context else {}
    started_at = datetime.now(timezone.utc).isoformat()
    path = state["evidence"] / f"docker-{len(state['invocations']):03d}.json"
    try:
        result = execute(command, state["evidence"], prompt=prompt, timeout=timeout,
                         output_limit=output_limit, env=environment, inherit_env=False)
    except BaseException as exc:
        save_json(path, {"argv": command, "environment": environment, "interruption": type(exc).__name__,
                         "detail": str(exc), "started_at": started_at,
                         "ended_at": datetime.now(timezone.utc).isoformat()})
        state["invocations"].append(str(path))
        raise
    record = {"argv": command, "environment": environment, "process": result,
              "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat()}
    save_json(path, record)
    state["invocations"].append(str(path))
    state["last_invocation"] = record
    return result


def _complete_result(result):
    return not any(result.get(key) for key in ("timed_out", "termination_error", "stdout_truncated", "stderr_truncated"))


def _json_result(result, *, array=False):
    require(result["returncode"] == 0 and _complete_result(result),
            "Docker command did not return complete successful evidence")
    value = parse_json(result["stdout"])
    if array:
        require(type(value) is list and len(value) == 1, "Docker readback must identify one object")
        value = value[0]
    require(type(value) is dict, "Docker readback must be an object")
    return value


def preflight(profile, evidence_dir):
    """Read actual local identities; never pull an image or create a container."""
    state = {"profile": profile, "evidence": Path(evidence_dir), "invocations": [],
             "config": Path(evidence_dir) / "docker-config"}
    state["config"].mkdir()
    try:
        require(profile["endpoint"].startswith(("unix:///", "npipe:////")), "Only local Docker endpoints are supported")
        require(PROBE_PYTHON in profile["runtime_paths"], "Prepared image must declare the fixed probe interpreter")
        executable = Path(profile["docker_executable"]).resolve(strict=True)
        state["executable_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
        context = _json_result(invoke(state, ["context", "inspect", profile["context"]], context=True), array=True)
        require(context.get("Name") == profile["context"], "Docker context identity changed")
        require(context.get("Endpoints", {}).get("docker", {}).get("Host") == profile["endpoint"],
                "Docker endpoint changed")
        version = _json_result(invoke(state, ["version", "--format", "{{json .}}"] ))
        require(version.get("Server", {}).get("Version") == profile["server_version"], "Docker server version changed")
        client = version["Client"]
        require(all(isinstance(client.get(key), str) and client[key] for key in ("Version", "Os", "Arch")),
                "Docker client identity is incomplete")
        state["docker_version"] = client["Version"]
        captured = state["last_invocation"]
        state["execution"] = {key: captured[key] for key in ("argv", "started_at", "ended_at")}
        state["execution"].update(returncode=captured["process"]["returncode"],
                                  executable_sha256=state["executable_sha256"], version=client["Version"],
                                  platform=client["Os"] + "/" + client["Arch"])
        info = _json_result(invoke(state, ["info", "--format", "{{json .}}"] ))
        require(info.get("ID") == profile["server_id"] and info.get("ServerVersion") == profile["server_version"],
                "Docker server identity changed")
        image = _json_result(invoke(state, ["image", "inspect", "--platform", profile["platform"], profile["image_digest"]]), array=True)
        platform = "/".join(part for part in (image.get("Os"), image.get("Architecture"), image.get("Variant")) if part)
        require(platform == profile["platform"], "Prepared image platform changed")
        digest = profile["image_digest"].split("@")[-1]
        require(image.get("Id") == digest or any(item.split("@")[-1] == digest for item in image.get("RepoDigests", [])),
                "Prepared image digest changed")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", "")), "Invalid selected image identity")
        require(not image.get("Config", {}).get("Volumes"), "Prepared image declares implicit volume grants")
        state["image_id"] = image["Id"]
        return state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        save_json(Path(evidence_dir) / "preflight-error.json", {"error": str(exc)})
        raise ValueError(f"unsupported-profile: {exc}; evidence: {evidence_dir}") from exc


def _inspect(state, identifier):
    return _json_result(invoke(state, ["container", "inspect", identifier]), array=True)


def _owned(value, identifier, owner):
    require(value.get("Id") == identifier and value.get("Config", {}).get("Labels", {}).get(LABEL) == owner,
            "Container identity or ownership changed")


def _grants(value, profile, layout, image_id):
    host = value["HostConfig"]
    require(value["Config"].get("Image") == profile["image_digest"], "Container requested image reference differs")
    descriptor = value.get("ImageManifestDescriptor")
    if descriptor is None:
        require(value.get("Image") == image_id, "Container uses a different selected image")
    else:
        require(type(descriptor) is dict and descriptor.get("digest") == image_id,
                "Container selected manifest differs")
        platform = descriptor.get("platform", {})
        observed_platform = "/".join(part for part in (platform.get("os"), platform.get("architecture"), platform.get("variant")) if part)
        require(observed_platform == profile["platform"], "Container selected platform differs")
        require(value.get("Image") in {profile["image_digest"].split("@")[-1], image_id},
                "Container image is neither the requested root nor selected manifest")
    require(value["Config"].get("User") == layout["user"], "Container user differs")
    require(host.get("ReadonlyRootfs") is True and host.get("Privileged") is False,
            "Container root/privilege policy differs")
    require(host.get("PidMode", "") == "" and host.get("IpcMode") == "private", "Container shares host process state")
    require(host.get("NetworkMode") == ("none" if layout["network"] == "none" else "bridge"), "Container network differs")
    require(host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"), "Container capability policy differs")
    require("no-new-privileges" in host.get("SecurityOpt", []), "Container privilege escalation is enabled")
    require(host.get("RestartPolicy", {}).get("Name") == "no", "Container may restart")
    limits = profile["limits"]
    require(host.get("Memory") == limits["memory_bytes"] and host.get("PidsLimit") == limits["pids"]
            and host.get("NanoCpus") == int(limits["cpus"] * 1_000_000_000), "Container resource limits differ")
    actual = sorted(({"source": item["Source"], "target": item["Destination"], "read_only": not item["RW"]}
                     for item in value.get("Mounts", []) if item.get("Type") == "bind"), key=lambda item: item["target"])
    require(len(actual) == len(value.get("Mounts", [])) and actual == layout["mounts"], "Container mount grants differ")
    require(value["Config"].get("Entrypoint") == [PROBE_PYTHON], "Container entrypoint differs")
    require(value["Config"].get("Healthcheck", {}).get("Test") == ["NONE"], "Container healthcheck is enabled")


def run_owned(profile, layout, argv, *, cwd, env, prompt, timeout, output_limit, evidence_dir):
    """Own one fresh container, retaining process and lifecycle as separate results."""
    state = preflight(profile, evidence_dir)
    owner = uuid.uuid4().hex
    limits = profile["limits"]
    cidfile = Path(evidence_dir) / "container.cid"
    command = ["create", "--pull=never", "--platform", profile["platform"], "--cidfile", str(cidfile), "--interactive", "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--ipc=private", "--network", "none" if layout["network"] == "none" else "bridge",
               "--user", layout["user"], "--memory", str(limits["memory_bytes"]), "--pids-limit", str(limits["pids"]),
               "--cpus", str(limits["cpus"]), "--restart=no", "--no-healthcheck", "--entrypoint", PROBE_PYTHON,
               "--label", f"{LABEL}={owner}"]
    for mount in layout["mounts"]:
        # Docker's mount grammar has no escaping for commas in a source path.
        require("," not in mount["source"], "Unsupported comma in mount source")
        command += ["--mount", f"type=bind,source={mount['source']},target={mount['target']}" + (",readonly" if mount["read_only"] else "")]
    command += [profile["image_digest"], "-I", "-B", "-c", LAUNCHER,
                json.dumps({"argv": argv, "cwd": cwd, "env": env}, separators=(",", ":"))]
    lifecycle = {"status": "unverified", "reason": "Owned container has not been removed", "evidence": state["invocations"]}
    process, failure, created = None, None, None
    try:
        created = invoke(state, command)
    except BaseException as exc:
        failure = exc
    recorded_id = ""
    try:
        if cidfile.exists() or cidfile.is_symlink():
            require(not cidfile.is_symlink() and cidfile.is_file(), "Container cidfile is not a regular file")
            recorded_id = cidfile.read_text(encoding="ascii").strip()
            require(re.fullmatch(r"[0-9a-f]{64}", recorded_id), "Container cidfile has an invalid exact ID")
    except (OSError, UnicodeError, ValueError) as exc:
        recorded_id = ""
        failure = failure or ValueError(f"Container cidfile could not be validated: {exc}")
    stdout_id = "" if created is None else created.get("stdout", "").strip()
    identifier = recorded_id if re.fullmatch(r"[0-9a-f]{64}", recorded_id) else stdout_id
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        lifecycle["reason"] = "Creation returned no recoverable exact ID; cleanup cannot be confirmed"
        save_json(Path(evidence_dir) / "lifecycle.json", {"container_id": None, "owner": owner, **lifecycle})
        if failure is not None:
            raise failure
        raise ValueError(f"unsupported-profile: Container creation returned no recoverable owned ID; evidence: {evidence_dir}")
    if created is None or created["returncode"] != 0 or not _complete_result(created):
        failure = failure or ValueError("Container creation did not complete successfully")
    elif stdout_id != identifier or created.get("stdout_truncated"):
        failure = ValueError("Container create stdout and owned cidfile identity differ")
    try:
        observed = _inspect(state, identifier)
        _owned(observed, identifier, owner)
        _grants(observed, profile, layout, state["image_id"])
        if failure is not None:
            raise failure
        process = invoke(state, ["start", "--attach", "--interactive", identifier], prompt=prompt,
                         timeout=timeout, output_limit=output_limit)
    except BaseException as exc:
        failure = exc
    finally:
        try:
            observed = _inspect(state, identifier)
            _owned(observed, identifier, owner)
            runtime_error = observed.get("State", {}).get("Error", "")
            if (failure is None and process is not None and process["returncode"] != 0
                    and observed.get("State", {}).get("Status") == "created"
                    and isinstance(runtime_error, str) and PROBE_PYTHON in runtime_error
                    and ("exec:" in runtime_error or "stat " in runtime_error)):
                failure = ValueError(f"unsupported-profile: Fixed image interpreter could not start: {runtime_error}; evidence: {evidence_dir}")
            if observed.get("State", {}).get("Running"):
                killed = invoke(state, ["kill", identifier])
                require(killed["returncode"] == 0 and _complete_result(killed), "Could not confirm kill of the owned container")
                observed = _inspect(state, identifier)
                _owned(observed, identifier, owner)
            require(observed.get("State", {}).get("Running") is False and observed.get("State", {}).get("Pid") == 0,
                    "Owned container did not reach a stopped state")
            denial_error = None
            try:
                stopped_exec = invoke(state, ["exec", identifier, PROBE_PYTHON, "-I", "-B", "-c", "pass"])
                require(stopped_exec["returncode"] != 0 and _complete_result(stopped_exec)
                        and identifier in stopped_exec["stderr"]
                        and "not running" in stopped_exec["stderr"].lower(),
                        "Stopped-container execution denial was not established")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                denial_error = str(exc)
            # Ownership and terminal state are established independently of this diagnostic.
            removed = invoke(state, ["rm", identifier])
            require(removed["returncode"] == 0 and _complete_result(removed), "Could not confirm removal of the stopped owned container")
            absent = invoke(state, ["container", "ls", "--all", "--no-trunc", "--filter", f"id={identifier}", "--format", "{{.ID}}"])
            require(absent["returncode"] == 0 and _complete_result(absent) and not absent["stdout"].strip(),
                    "Owned container removal could not be confirmed")
            lifecycle.update(status="failed" if denial_error else "verified",
                             reason=denial_error or "Exact owned container stopped and removal confirmed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            lifecycle.update(status="failed", reason=str(exc))
        save_json(Path(evidence_dir) / "lifecycle.json", {"container_id": identifier, "owner": owner, **lifecycle})
    if failure is not None:
        if isinstance(failure, (KeyboardInterrupt, SystemExit)) or str(failure).startswith("unsupported-profile:"):
            raise failure
        raise ValueError(f"isolation-unqualified: {failure}; lifecycle {lifecycle['status']}; evidence: {evidence_dir}") from failure
    return {"process": process, "container_id": identifier, "lifecycle": lifecycle,
            "identity": {key: state[key] for key in ("executable_sha256", "docker_version", "image_id")}}
