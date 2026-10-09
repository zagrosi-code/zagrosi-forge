"""Role-derived grants for the opt-in suite's owned Docker execution."""
from __future__ import annotations

from pathlib import Path

from coding_trial_inventory import contains_path, fingerprint, inventory
from coding_trial_manifest import validate_suite
from coding_trial_qualification import fields, parse_json, read_bytes, require

ADAPTER_SOURCES = ("tools/coding_trial_isolation.py", "tools/coding_trial_docker.py", "tools/coding_trial_process.py",
                   "tools/coding_trial_inventory.py", "tools/coding_trial_qualification.py", "tools/coding_trial_manifest.py",
                   "tools/coding_trial_native.py", "tools/coding_trial_loading.py", "tools/coding_trial_loading_evidence.py",
                   "tools/coding_trial_reservation.py", "scripts")


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def _directory(value):
    require(isinstance(value, str), "Runtime root must be a path string")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "Runtime root must be absolute")
    resolved = path.resolve(strict=True)
    require(path == resolved and resolved.is_dir(), "Runtime root must be a resolved directory")
    return resolved


def _private_path(value):
    require(isinstance(value, str), "Private root must be a path string")
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "Private root must be absolute")
    if not path.exists():
        require(path.parent.is_dir(), "Missing private-root parent")
    require(path == path.resolve(strict=False), "Private root must not use a symlink alias")
    return path


def suite_context(suite, suite_root, task_id, arm_id, roots):
    """Revalidate selected inputs and root separation without creating an attempt."""
    suite = validate_suite(suite, suite_root)
    try:
        require(task_id in suite["tasks"] and arm_id in suite["arms"], "Unknown task or arm")
        fields(roots, "workspace product public generated native_runtime private", "runtime roots")
        paths = {key: _directory(roots[key]) for key in ("workspace", "product", "public", "generated")}
        paths["native_runtime"] = None if roots["native_runtime"] is None else _directory(roots["native_runtime"])
        require(type(roots["private"]) is list and roots["private"], "Private roots must be nonempty")
        private = [_private_path(value) for value in roots["private"]]
        public = [path for path in paths.values() if path is not None]
        require(not any(_overlap(left, right) for index, left in enumerate(public) for right in public[index + 1:]),
                "Runtime roots overlap")
        require(not any(_overlap(source, target) for source in public for target in private),
                "Runtime root overlaps private material")
        paths["private"] = private
        product = suite["arms"][arm_id]["product"]
        expected = fingerprint({}) if product is None else product["inventory_sha256"]
        require(fingerprint(inventory(paths["product"])) == expected, "Prepared product differs from the selected arm")
        return suite, paths
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise ValueError(f"suite-invalid: {exc}") from exc


def fresh_evidence_path(value, paths):
    """Validate private evidence destination before any mutation or process starts."""
    path = Path(value)
    if path.exists() or path.is_symlink():
        raise ValueError(f"input-exists: {path}")
    try:
        path = _private_path(str(path))
        require(any(path.is_relative_to(root) for root in paths["private"]),
                "Evidence must stay beneath declared private roots")
        return path
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise ValueError(f"suite-invalid: {exc}") from exc


def derive_layout(suite, suite_root, task_id, arm_id, roots, *, role, auth_file=None):
    """Derive exact grants from prepared roles; this does not admit a live host."""
    native_writer = (role == "writer" and isinstance(suite, dict) and isinstance(suite.get("host"), dict)
                     and suite["host"].get("adapter") == "codex")
    if auth_file is not None and not native_writer:
        raise ValueError("suite-invalid: Only a native writer may receive credentials")
    if native_writer:
        if suite["host"].get("isolation") is None:
            raise ValueError("unsupported-profile: An explicit Docker profile is required")
        from coding_trial_native import read_native_runtime
        read_native_runtime(suite, suite_root, task_id, arm_id, roots, auth_file=auth_file)
    suite, paths = suite_context(suite, suite_root, task_id, arm_id, roots)
    isolation = suite["host"]["isolation"]
    if isolation is None:
        raise ValueError("unsupported-profile: An explicit Docker profile is required")
    try:
        require(role in ("writer", "native", "worker"), "Unknown execution role")
        require(native_writer or (paths["native_runtime"] is None and auth_file is None),
                "Only a native writer may receive native runtime or credentials")
        profile = parse_json(read_bytes(Path(suite_root).resolve(strict=True), isolation["profile"]))
        mounts = [{"source": str(paths["workspace"]), "target": "/workspace", "read_only": role != "writer"}]
        if role == "writer":
            mounts.append({"source": str(paths["product"]), "target": "/product", "read_only": True})
            if native_writer:
                for name, target, read_only in (("home", "/home/forge", False), ("codex", "/codex", False),
                                                ("plugins", "/codex/plugins", True), ("marketplace", "/marketplace", True)):
                    mounts.append({"source": str(paths["native_runtime"] / name), "target": target, "read_only": read_only})
                if auth_file is not None:
                    mounts.append({"source": str(Path(auth_file)), "target": "/codex/auth.json", "read_only": True})
        else:
            checks = suite["tasks"][task_id]["checks"]
            commands = checks["native"] if role == "native" else [checks["worker"]]
            resources = sorted({name for command in commands
                                for name in [command["entry"], *command["support"]] if name is not None})
            expected = inventory(Path(suite_root).resolve(strict=True), included=tuple(resources))
            require(inventory(paths["public"]) == expected, "Public checks differ from their declared role")
            mounts.append({"source": str(paths["public"]), "target": "/checks", "read_only": True})
            generated = sorted(suite["tasks"][task_id]["scope"]["generated"])
            outermost = [name for name in generated if not any(
                parent != name and contains_path(parent, name) for parent in generated)]
            for name in outermost:
                source = _directory(str(paths["generated"] / name))
                target = _directory(str(paths["workspace"] / name))
                require(not inventory(source) and not inventory(target), "Generated mountpoints must start empty")
                mounts.append({"source": str(source), "target": f"/workspace/{name}", "read_only": False})
        return {"role": role, "task": task_id, "arm": arm_id, "mounts": sorted(mounts, key=lambda item: item["target"]),
                "user": profile["user"], "network": profile["network"]}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise ValueError(f"suite-invalid: {exc}") from exc


def network_policy(mode, observations):
    """Interpret complete Linux network observations independently of host OS."""
    if not isinstance(observations, dict) or "network_connection" not in observations:
        return False
    interfaces = observations.get("network_interfaces")
    if not isinstance(interfaces, list) or not interfaces:
        return False
    names, active_nonloopback = set(), False
    for interface in interfaces:
        if not isinstance(interface, dict) or set(interface) != {"name", "flags"}:
            return False
        name, flags = interface["name"], interface["flags"]
        if not isinstance(name, str) or not name or name in names or type(flags) is not int or flags < 0:
            return False
        names.add(name)
        active_nonloopback |= bool(flags & 0x1 and not flags & 0x8)  # IFF_UP, IFF_LOOPBACK
    connection = observations["network_connection"]
    if mode == "outbound-enabled":
        return connection is None and active_nonloopback
    if mode != "none" or not isinstance(connection, dict) or set(connection) != {"succeeded", "errno"}:
        return False
    # Linux image values: ENETUNREACH, EHOSTUNREACH, EACCES, EPERM.
    return (not active_nonloopback and connection["succeeded"] is False
            and type(connection["errno"]) is int and connection["errno"] in {101, 113, 13, 1})


ACCESS_PROBE = r'''
import hashlib,json,os,pathlib,socket,subprocess,sys
s=json.loads(sys.argv[1]); private=pathlib.Path(s['private']); scratch=pathlib.Path(s['scratch'])
def denied(call):
    try: call()
    except OSError: return True
    return False
marker=pathlib.Path(s['task_marker'])
r={'task_read':marker.read_text()==s['nonce']}
write=lambda: marker.write_text('probe-write')
r['task_write_policy']=not denied(write) if s['role']=='writer' else denied(write)
if s['writable']:
    target=pathlib.Path(s['writable'])/'allowed'
    target.write_text(s['nonce']); r['scratch_write']=target.read_text()==s['nonce']
else: r['scratch_write']=True
r['private_read']=denied(private.read_bytes)
r['private_write']=denied(lambda:private.write_text('probe-write'))
r['symlink_read']=denied((scratch/'private-link').read_bytes)
r['relative_read']=denied(lambda:pathlib.Path(os.path.relpath(private,pathlib.Path.cwd())).read_bytes())
r['relative_link_read']=denied((scratch/'relative-private-link').read_bytes)
product=pathlib.Path('/product')
r['product_read_policy']=(not denied(lambda:list(product.iterdir())) if s['role']=='writer'
                          else not product.exists() and not product.is_symlink() and denied(lambda:list(product.iterdir())))
r['product_write_policy']=denied(lambda:(product/('readonly-'+s['nonce'])).write_text('probe-write'))
r['readonly_roots']=all(denied(lambda p=pathlib.Path(p): (p/('readonly-'+s['nonce'])).write_text('probe-write')) for p in s['readonly'])
r['public_read']=all(pathlib.Path(p).is_dir() and isinstance(list(pathlib.Path(p).iterdir()),list) for p in s['readonly'])
r['root_write']=denied(lambda:pathlib.Path('/'+s['nonce']).write_text('probe-write'))
r['docker_socket']=not pathlib.Path('/var/run/docker.sock').exists()
child=subprocess.run([sys.executable,'-I','-B','-c','from pathlib import Path; Path('+repr(str(private))+').read_bytes()'],capture_output=True,text=True,env={})
r['child_private']=child.returncode!=0 and ('FileNotFoundError' in child.stderr or 'PermissionError' in child.stderr)
r['network_interfaces']=[{'name':name,'flags':int((pathlib.Path('/sys/class/net')/name/'flags').read_text(),16)}
                         for _,name in socket.if_nameindex()]
r['network_connection']=None
if s['network']=='none':
    try:
        with socket.create_connection(('192.0.2.1',9),.2):
            r['network_connection']={'succeeded':True,'errno':None}
    except OSError as error:
        r['network_connection']={'succeeded':False,'errno':error.errno}
r['executable_sha256']=hashlib.sha256(pathlib.Path(sys.executable).read_bytes()).hexdigest()
print(json.dumps(r))
'''

DETACHED_PROBE = r'''
import json,os,select,subprocess,sys,time
s=json.loads(sys.argv[1]); read_fd,write_fd=os.pipe()
child="""import json,os,pathlib,sys,time
s=json.loads(sys.argv[1]); fd=int(sys.argv[2]); os.write(fd,json.dumps({'nonce':s['nonce'],'pid':os.getpid()}).encode()); os.close(fd)
deadline=time.monotonic()+6
while time.monotonic()<deadline:
    if s['marker'] and (pathlib.Path(s['marker'])/'release').exists():
        (pathlib.Path(s['marker'])/'survived').write_text(s['nonce']); break
    time.sleep(.01)
"""
subprocess.Popen([sys.executable,'-I','-B','-c',child,json.dumps(s),str(write_fd)],pass_fds=(write_fd,),start_new_session=True,
                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env={})
os.close(write_fd)
if not select.select([read_fd],[],[],5)[0]: raise RuntimeError('detached child did not become ready')
print(os.read(read_fd,4096).decode(),flush=True); os.close(read_fd)
if s['wait']: time.sleep(30)
'''


def _prepared_execution(suite, suite_root, layout, roots, evidence_dir, auth_file):
    """Complete pure checks before creating evidence or contacting Docker."""
    try:
        fields(layout, "role task arm mounts user network", "execution layout")
        expected = derive_layout(suite, suite_root, layout["task"], layout["arm"], roots,
                                 role=layout["role"], auth_file=auth_file)
        require(layout == expected, "Supplied execution layout differs from derived grants")
        _, paths = suite_context(suite, suite_root, layout["task"], layout["arm"], roots)
        destination = fresh_evidence_path(evidence_dir, paths)
        profile = parse_json(read_bytes(Path(suite_root).resolve(strict=True), suite["host"]["isolation"]["profile"]))
        require(not any("," in item["source"] for item in layout["mounts"]), "Unsupported comma in mount source")
        return profile, paths, destination
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        if str(exc).startswith(("suite-invalid:", "input-exists:", "unsupported-profile:", "loading-unqualified:")):
            raise
        raise ValueError(f"suite-invalid: {exc}") from exc


def _create_evidence(path):
    try:
        path.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"input-exists: {path}") from exc


def _probe_result(result):
    process = result["process"]
    require(process["returncode"] == 0 and not process.get("timed_out")
            and not process.get("stdout_truncated") and not process.get("termination_error"), "Incomplete probe execution")
    value = parse_json(process["stdout"])
    require(type(value) is dict, "Probe must return one JSON object")
    return value


def _tree_observations(paths):
    return {key: inventory(paths[key], excluded=(".git",) if key == "workspace" else ())
            for key in ("workspace", "product", "public", "generated")}


def qualify_profile(suite, suite_root, layout, evidence_dir, *, roots, auth_file=None, cancel_event=None):
    """Run actual access and detached-child controls under the exact role grants."""
    import hashlib
    import json
    import os
    import posixpath
    import shutil
    import sys
    import time
    import uuid
    from datetime import datetime, timezone
    from coding_trial_docker import PROBE_PYTHON, _check_cancelled, preflight, run_owned, save_json
    from coding_trial_process import execute
    from coding_trial_qualification import CHECKS

    profile, paths, evidence_dir = _prepared_execution(suite, suite_root, layout, roots, evidence_dir, auth_file)
    _create_evidence(evidence_dir)
    started = datetime.now(timezone.utc).isoformat()
    clock_started = time.monotonic()
    # Availability is read-only and retained before any candidate/root mutation.
    initial = evidence_dir / "preflight"
    initial.mkdir()
    options = {} if cancel_event is None else {"cancel_event": cancel_event}
    identity = preflight(profile, initial, **options)
    _check_cancelled(cancel_event, evidence_dir)
    nonce = "forge-probe-" + uuid.uuid4().hex
    scratch = paths["workspace"] / nonce
    private = evidence_dir / "private-control.txt"
    private.write_text(nonce, encoding="utf-8")
    before = _tree_observations(paths)
    scratch.mkdir()
    (scratch / "task-read.txt").write_text(nonce, encoding="utf-8")
    (scratch / "private-link").symlink_to(private)
    (scratch / "relative-private-link").symlink_to(posixpath.relpath(str(private), "/workspace/" + nonce))
    writable_mount = next((mount for mount in layout["mounts"]
                           if not mount["read_only"] and mount["target"].startswith("/workspace")), None)
    writable = None
    if writable_mount is not None:
        writable = Path(writable_mount["source"]) / nonce
        if writable != scratch:
            writable.mkdir()
    checks = {key: False for key in CHECKS["isolation"] | set(profile["network_checks"])}
    outcomes, error = [], None
    try:
        # Same private bytes are readable without the Docker boundary.
        _check_cancelled(cancel_event, evidence_dir)
        control_script = "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text())"
        control = execute([sys.executable, "-I", "-B", "-c", control_script, str(private)], evidence_dir,
                          timeout=5, output_limit=1024, env={}, inherit_env=False, **options)
        save_json(evidence_dir / "unrestricted-read.json", control)
        _check_cancelled(cancel_event, evidence_dir)
        unrestricted_read = control["returncode"] == 0 and control["stdout"].strip() == nonce
        control_dir = evidence_dir / "unrestricted-child"
        control_dir.mkdir()
        _check_cancelled(cancel_event, evidence_dir)
        # Retain the detached control's handshake and finish it before honoring cancellation.
        detached = execute([sys.executable, "-I", "-B", "-c", DETACHED_PROBE,
                            json.dumps({"nonce": nonce, "marker": str(control_dir), "wait": False})],
                           evidence_dir, timeout=8, output_limit=4096, env={}, inherit_env=False)
        save_json(evidence_dir / "unrestricted-child.json", detached)
        ready = parse_json(detached["stdout"])
        require(detached["returncode"] == 0 and ready.get("nonce") == nonce
                and type(ready.get("pid")) is int and ready["pid"] > 0, "Unrestricted detached control was not ready")
        (control_dir / "release").write_text(nonce, encoding="utf-8")
        deadline = time.monotonic() + 7
        while not (control_dir / "survived").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        unrestricted_child = (control_dir / "survived").read_text() == nonce
        _check_cancelled(cancel_event, evidence_dir)
        writable_target = None if writable_mount is None else writable_mount["target"] + "/" + nonce
        read_only = [mount["target"] for mount in layout["mounts"] if mount["read_only"] and mount["target"] != "/codex/auth.json"]
        spec = {"private": str(private), "scratch": "/workspace/" + nonce,
                "task_marker": "/workspace/" + nonce + "/task-read.txt", "nonce": nonce,
                "role": layout["role"], "writable": writable_target, "readonly": read_only, "network": profile["network"]}
        access_dir = evidence_dir / "access"
        access_dir.mkdir()
        access = run_owned(profile, layout, [PROBE_PYTHON, "-I", "-B", "-c", ACCESS_PROBE, json.dumps(spec)],
                           cwd="/workspace", env={}, prompt=None, timeout=15, output_limit=16384, evidence_dir=access_dir, **options)
        outcomes.append(access)
        observed = _probe_result(access)
        save_json(evidence_dir / "access-observations.json", observed)
        checks.update({
            "task-read-write": all(observed.get(key) is True for key in ("task_read", "task_write_policy", "scratch_write")),
            "product-read-only": all(observed.get(key) is True for key in
                                     ("product_read_policy", "product_write_policy", "readonly_roots", "public_read", "root_write", "docker_socket")),
            "private-read-denied": unrestricted_read and observed.get("private_read") is True,
            "private-write-denied": observed.get("private_write") is True and private.read_text() == nonce,
            "relative-symlink-denied": all(observed.get(key) is True for key in
                                          ("symlink_read", "relative_read", "relative_link_read")),
            "child-private-denied": observed.get("child_private") is True,
            profile["network_checks"][0]: network_policy(profile["network"], observed),
        })
        detached_ok = unrestricted_child
        for mode in ("normal", "timeout"):
            _check_cancelled(cancel_event, evidence_dir)
            if writable is not None:
                marker = writable / mode
                marker.mkdir()
                target = writable_target + "/" + mode
            else:
                marker, target = None, None
            folder = evidence_dir / ("detached-" + mode)
            folder.mkdir()
            result = run_owned(profile, layout, [PROBE_PYTHON, "-I", "-B", "-c", DETACHED_PROBE,
                               json.dumps({"nonce": nonce, "marker": target, "wait": mode == "timeout"})],
                               cwd="/workspace", env={}, prompt=None, timeout=2 if mode == "timeout" else 8,
                               output_limit=4096, evidence_dir=folder, **options)
            outcomes.append(result)
            process = result["process"]
            observed_ready = parse_json(process["stdout"])
            ok = (observed_ready.get("nonce") == nonce and type(observed_ready.get("pid")) is int
                  and observed_ready["pid"] > 0 and not process.get("stdout_truncated")
                  and result["lifecycle"]["status"] == "verified")
            ok = ok and (process.get("timed_out") is True if mode == "timeout" else process["returncode"] == 0)
            if marker is not None:
                (marker / "release").write_text(nonce, encoding="utf-8")
                time.sleep(.2)
                ok = ok and not (marker / "survived").exists()
            save_json(folder / "observation.json", {"nonce": nonce, "ready": observed_ready,
                      "post_release_write": None if marker is None else (marker / "survived").exists(), "passed": bool(ok)})
            detached_ok = detached_ok and ok
        checks["detached-child-stopped"] = bool(detached_ok)
        checks["owned-container-removed"] = all(item["lifecycle"]["status"] == "verified" for item in outcomes)
        _check_cancelled(cancel_event, evidence_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = exc
        save_json(evidence_dir / "probe-error.json", {"error": str(exc)})
    finally:
        # Only fresh controller-created scratch paths are removed.
        for mount in layout["mounts"]:
            if mount["read_only"] and Path(mount["source"]).is_dir():
                probe_write = Path(mount["source"]) / ("readonly-" + nonce)
                if probe_write.exists() and not probe_write.is_symlink():
                    probe_write.unlink()
        if writable is not None and writable != scratch:
            shutil.rmtree(writable)
        shutil.rmtree(scratch)
        unchanged = _tree_observations(paths) == before
        save_json(evidence_dir / "restoration.json", {"public_roots_unchanged": unchanged})
        if not unchanged:
            checks = dict.fromkeys(checks, False)
    if error is not None and str(error).startswith(("unsupported-profile:", "execution-cancelled:")):
        raise error
    _check_cancelled(cancel_event, evidence_dir)
    save_json(evidence_dir / "timing.json", {"started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
              "seconds": time.monotonic() - clock_started})
    source_root = Path(__file__).resolve().parents[1]
    raw = sorted(path for path in evidence_dir.rglob("*") if path.is_file())
    evidence = [{"path": path.relative_to(evidence_dir).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in raw]
    references = [item["path"] for item in evidence]
    access_refs = [path for path in references if path.startswith("access/") or path == "access-observations.json"]
    detached_refs = [path for path in references if path.startswith(("detached-", "unrestricted-child"))]
    check_refs = {key: access_refs + ["restoration.json"] for key in checks}
    check_refs["private-read-denied"] += ["unrestricted-read.json", "private-control.txt"]
    check_refs["private-write-denied"] += ["private-control.txt"]
    check_refs["detached-child-stopped"] = detached_refs + ["restoration.json"]
    check_refs["owned-container-removed"] = [path for path in references if path.endswith("/lifecycle.json")] + ["restoration.json"]
    if "probe-error.json" in references:
        for key, passed in checks.items():
            if not passed:
                check_refs[key].append("probe-error.json")
    receipt = {
        "schema": "coding-trial-qualification/v1", "kind": "isolation",
        "bindings": {"docker_executable_sha256": identity["executable_sha256"], "docker_version": identity["docker_version"],
            **{key: profile[key] for key in ("context", "endpoint", "server_id", "server_version", "image_digest", "platform", "network")},
            "adapter_sha256": fingerprint(inventory(source_root, included=ADAPTER_SOURCES)),
            "probe_sha256": fingerprint(inventory(source_root, included=("tools/coding_trial_isolation.py", "tools/coding_trial_docker.py"))),
            "profile_sha256": hashlib.sha256(read_bytes(Path(suite_root), suite["host"]["isolation"]["profile"])).hexdigest(),
            "layout_sha256": fingerprint({key: layout[key] for key in ("mounts", "user", "network")}),
            "limits_sha256": fingerprint(profile["limits"])},
        "checks": [{"id": key, "status": "passed" if value else "failed",
                    "evidence": sorted(set(check_refs[key]) & set(references))} for key, value in sorted(checks.items())],
        "producer": {"name": "coding_trial_isolation.qualify_profile", "independent": False,
                     "purpose": "actual" if suite["purpose"] == "prospective" else "synthetic"},
        "execution": identity["execution"],
        "evidence": evidence,
    }
    save_json(evidence_dir / "qualification.json", receipt)
    return receipt


def execute_isolated(suite, suite_root, layout, argv, *, roots, cwd, env, prompt, timeout,
                     output_limit, evidence_dir, auth_file=None, cancel_event=None):
    """Requalify this exact exposure, then run one owned process without inheritance."""
    import hashlib
    from pathlib import PurePosixPath
    from coding_trial_docker import _check_cancelled, run_owned
    from coding_trial_qualification import environment, integer, positive, strings

    profile, _, evidence_dir = _prepared_execution(suite, suite_root, layout, roots, evidence_dir, auth_file)
    try:
        strings(argv, "execution argv", nonempty=True)
        environment(env, "execution environment")
        require(isinstance(cwd, str) and PurePosixPath(cwd).is_absolute() and ".." not in PurePosixPath(cwd).parts,
                "Container cwd must be absolute")
        require(prompt is None or isinstance(prompt, str), "Invalid execution prompt")
        positive(timeout, "timeout")
        require(timeout <= 86400, "Timeout exceeds shared executor limit")
        integer(output_limit, "output_limit", minimum=1)
        require(output_limit <= 8388608, "Output exceeds shared executor limit")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"suite-invalid: {exc}") from exc
    _create_evidence(evidence_dir)
    _check_cancelled(cancel_event, evidence_dir)
    options = {} if cancel_event is None else {"cancel_event": cancel_event}
    qualification_path = evidence_dir / "qualification"
    qualification = qualify_profile(suite, suite_root, layout, qualification_path, roots=roots, auth_file=auth_file, **options)
    _check_cancelled(cancel_event, evidence_dir)
    if any(check["status"] != "passed" for check in qualification["checks"]):
        raise ValueError(f"isolation-unqualified: Actual probes failed; evidence: {qualification_path}")
    # Probe scratch is gone; rederive current grants before the actual process.
    require(layout == derive_layout(suite, suite_root, layout["task"], layout["arm"], roots,
                                    role=layout["role"], auth_file=auth_file), "Current layout changed after qualification")
    execution = evidence_dir / "execution"
    execution.mkdir()
    result = run_owned(profile, layout, argv, cwd=cwd, env=env, prompt=prompt, timeout=timeout,
                       output_limit=output_limit, evidence_dir=execution, **options)
    receipt = qualification_path / "qualification.json"
    timing = parse_json((qualification_path / "timing.json").read_bytes())
    return {"process": result["process"], "isolation": {"adapter": "docker-v1",
            "status": "passed" if result["lifecycle"]["status"] == "verified" else "failed",
            "qualification_seconds": timing["seconds"],
            "qualification": {"path": str(receipt), "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()},
            "container_id": result["container_id"], "lifecycle": result["lifecycle"]}}
