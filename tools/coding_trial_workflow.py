"""One bounded evaluator-owned workflow observation in the writer's namespace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid

from coding_trial_docker import PROBE_PYTHON, run_owned
from coding_trial_inventory import fingerprint, inventory
from coding_trial_isolation import network_policy
from coding_trial_loading_evidence import complete
from coding_trial_qualification import _instant, fields, parse_json, read_bytes, require, sha
from coding_trial_study import _read_study, _reference, _save


ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_COUNT": "3",
    "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
    "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false",
    "GIT_CONFIG_KEY_2": "core.untrackedCache", "GIT_CONFIG_VALUE_2": "false",
}
OBSERVATIONS = ("task_read", "readonly_roots", "private_read", "private_write", "child_private",
                "root_write", "product_read_policy", "docker_socket")
PROBE = r'''
import hashlib,json,os,pathlib,socket,subprocess,sys
s=json.loads(sys.argv[1]); private=pathlib.Path(s['private'])
def denied(call):
    try: call()
    except (FileNotFoundError,PermissionError): return True
    except OSError as error: return error.errno==30
    return False
def write(path):
    with path.open('x') as stream: stream.write(s['nonce'])
r={'task_read':hashlib.sha256(pathlib.Path(s['marker']).read_bytes()).hexdigest()==s['marker_sha256']}
r['readonly_roots']=all(denied(lambda root=root:write(pathlib.Path(root)/('probe-'+s['nonce']))) for root in s['readonly'])
r['private_read']=denied(private.read_bytes)
r['private_write']=denied(lambda:write(private.with_name('probe-'+s['nonce'])))
child=subprocess.run([sys.executable,'-I','-B','-c','from pathlib import Path; Path('+repr(str(private))+').read_bytes()'],
                     capture_output=True,text=True,env=dict(os.environ),timeout=5)
r['child_private']=child.returncode!=0 and ('FileNotFoundError' in child.stderr or 'PermissionError' in child.stderr)
r['root_write']=denied(lambda:write(pathlib.Path('/probe-'+s['nonce'])))
product=pathlib.Path('/product')
r['product_read_policy']=not product.exists() and not product.is_symlink() and denied(lambda:list(product.iterdir()))
r['docker_socket']=not pathlib.Path('/var/run/docker.sock').exists()
r['executable_sha256']=hashlib.sha256(pathlib.Path(sys.executable).read_bytes()).hexdigest()
git=subprocess.run(['git','--version'],capture_output=True,text=True,env=dict(os.environ),timeout=5,check=True)
r['git_version']=git.stdout.strip()
r['network_interfaces']=[{'name':name,'flags':int((pathlib.Path('/sys/class/net')/name/'flags').read_text(),16)}
                         for _,name in socket.if_nameindex()]
try:
    with socket.create_connection(('192.0.2.1',9),.2): r['network_connection']={'succeeded':True,'errno':None}
except OSError as error: r['network_connection']={'succeeded':False,'errno':error.errno}
print(json.dumps(r))
'''


def _ref(trial, path):
    name = path.relative_to(trial).as_posix()
    return {"path": name, "sha256": hashlib.sha256(read_bytes(trial, name)).hexdigest()}


def _plan(workflow):
    candidates = {}
    for name, entry in workflow.items():
        path = PurePosixPath(name)
        index = name.endswith("/sections/index.md")
        if path.name not in {"codex-plan.md", "claude-plan.md"} and not index:
            continue
        require(entry["type"] == "file", "Workflow plan marker must be a regular file")
        root = path.parent.parent if index else path.parent
        candidates.setdefault(str(root), name)
    require(len(candidates) == 1, "Workflow needs exactly one declared planning root")
    return next(iter(candidates.items()))


def _call(trial, parent, name, profile, layout, argv, cwd, bounds, evidence):
    folder = parent / name
    folder.mkdir()
    result = None
    try:
        result = run_owned(profile, layout, argv, cwd=cwd, env=dict(ENV), prompt=None,
                           timeout=bounds[0], output_limit=bounds[1], evidence_dir=folder)
    finally:
        # Retain only executor records at this fixed step, including failed calls.
        for path in sorted(folder.glob("docker-*.json")):
            evidence.append(_ref(trial, path))
        for name in ("container.cid", "lifecycle.json", "preflight-error.json"):
            path = folder / name
            if path.exists() or path.is_symlink():
                evidence.append(_ref(trial, path))
        if result is not None:
            saved = folder / "result.json"
            _save(saved, {"argv": argv, "cwd": cwd, "environment": ENV, "prompt": None,
                          "timeout_seconds": bounds[0], "output_bytes": bounds[1], "result": result})
            evidence.append(_ref(trial, saved))
    lifecycle_path = folder / "lifecycle.json"
    lifecycle = parse_json(read_bytes(folder, lifecycle_path.name))
    require(lifecycle["container_id"] == result["container_id"]
            and all(lifecycle[key] == result["lifecycle"][key] for key in ("status", "reason", "evidence")),
            "Workflow lifecycle contradicts its retained record")
    starts = []
    start_command = [profile["docker_executable"], "--host", profile["endpoint"], "--config",
                     str(folder / "docker-config"), "start", "--attach", "--interactive", result["container_id"]]
    for name in result["lifecycle"]["evidence"]:
        path = Path(name)
        require(path.parent == folder and _ref(trial, path) in evidence, "Unbound workflow lifecycle evidence")
        record = parse_json(read_bytes(folder, path.name))
        if record.get("argv") == start_command:
            starts.append(record)
    require(len(starts) == 1, "Workflow needs exactly one retained Docker start")
    start = starts[0]
    fields(start, "argv environment process started_at ended_at", "Workflow Docker start")
    require(start["environment"] == {} and start["process"] == result["process"]
            and _instant(start["started_at"]) <= _instant(start["ended_at"]),
            "Workflow process contradicts its retained Docker start")
    require(result["lifecycle"]["status"] == "verified" and complete(result["process"]),
            "Workflow process or cleanup did not complete successfully")
    return parse_json(result["process"]["stdout"])


def _validate_workflow(trial, attempt, suite, suite_root, delivered, workflow, git):
    arm = suite["arms"][attempt["identity"]["arm"]]
    if arm["workflow"] != "forge-v1":
        return {"status": "not_applicable" if arm["workflow"] == "none" else "unmeasured",
                "detail": "No Forge workflow validation requested", "evidence": []}
    evidence = []
    summary = {"status": "failed", "detail": "Workflow context unavailable", "evidence": evidence}
    workspace = Path(attempt["roots"]["workspace"])
    try:
        writer = parse_json(_reference(trial, attempt["runner"]))
        require(writer["isolation"]["lifecycle"]["status"] == "verified", "Writer cleanup was not verified")
        planning, marker = _plan(workflow)
        study, _, _ = _read_study(attempt["study"])
        require(suite["host"]["isolation"] is not None and git["snapshot"] is not None,
                "Workflow requires a prepared Docker profile and valid captured Git")
        evaluator = Path(study["evaluator"]["root"])
        metadata = trial / git["snapshot"]["path"]
        expected_git = inventory(metadata)
        require(fingerprint(expected_git) == git["snapshot"]["inventory_sha256"]
                and inventory(workspace / ".git") == expected_git, "candidate-drift: Workflow Git differs")
        namespace = "/workspace" if suite["host"]["isolation"] is not None else str(workspace)
        target = PurePosixPath(namespace)
        require(target.is_absolute() and str(target) == namespace and target != PurePosixPath("/")
                and not (target.is_relative_to("/checks") or PurePosixPath("/checks").is_relative_to(target)),
                "Unsupported workflow namespace")
        parent = trial / "assessor/workflow-validation"
        parent.mkdir()
        nonce = uuid.uuid4().hex
        private = parent / "private-marker.txt"
        with private.open("x") as stream:
            stream.write(nonce)
        profile = parse_json(read_bytes(suite_root, suite["host"]["isolation"]["profile"]))
        mounts = [{"source": str(workspace), "target": namespace, "read_only": True},
                  {"source": str(metadata), "target": namespace + "/.git", "read_only": True},
                  {"source": str(evaluator), "target": "/checks", "read_only": True}]
        layout = {"role": "workflow", "task": attempt["identity"]["task"], "arm": attempt["identity"]["arm"],
                  "mounts": sorted(mounts, key=lambda item: item["target"]), "user": profile["user"], "network": "none"}
        spec = {"marker": namespace + "/" + marker, "marker_sha256": delivered[marker]["sha256"],
                "private": str(private), "readonly": [namespace, "/checks"], "nonce": nonce}
        observed = _call(trial, parent, "exposure", profile, layout,
                         [PROBE_PYTHON, "-I", "-B", "-c", PROBE, json.dumps(spec)], namespace, (15, 65536), evidence)
        require(type(observed) is dict and set(observed) == {*OBSERVATIONS, "executable_sha256", "git_version",
                                                           "network_interfaces", "network_connection"},
                "Incomplete workflow exposure observation")
        sha(observed["executable_sha256"])
        require(all(observed[key] is True for key in OBSERVATIONS) and network_policy("none", observed)
                and isinstance(observed["git_version"], str) and observed["git_version"].startswith("git version "),
                "Workflow exposure or runtime requirements failed")
        depth = arm["configuration"].get("depth")
        require(depth is None or depth in {"lean", "fast", "standard", "deep"}, "Unsupported workflow depth")
        argv = [PROBE_PYTHON, "-I", "-B", "/checks/tools/coding_trial_evidence.py", "/checks", namespace]
        if depth is not None:
            argv.append(depth)
        report = _call(trial, parent, "validation", profile, layout,
                       argv + ["--planning-dir", namespace + "/" + planning], namespace, (60, 12000), evidence)
        require(type(report) is dict and all(report.get(key) is True for key in
                ("success", "admission_success", "sections_recorded_complete")), "Forge workflow is incomplete or invalid")
        summary.update(status="passed", detail="Evaluator-owned admission and completion passed in the original namespace")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        if str(exc).startswith(("input-drift:", "candidate-drift:")):
            raise
        summary["detail"] = str(exc)
    require(inventory(workspace, excluded=(".git",)) == delivered, "candidate-drift: Workflow changed delivered files")
    if git["snapshot"] is not None:
        require(inventory(workspace / ".git") == inventory(trial / git["snapshot"]["path"]),
                "candidate-drift: Workflow changed Git metadata")
    _read_study(attempt["study"])
    for reference in evidence:
        _reference(trial, reference)
    return summary
