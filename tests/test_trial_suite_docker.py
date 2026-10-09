"""Synthetic Docker boundary controls, never actual isolation qualification.

Only a task-owned fake executable runs. It provides documented read-only CLI
responses and optional container metadata. It never runs container commands and
never supplies passing mechanism probe results.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_isolation import derive_layout, execute_isolated, qualify_profile
from test_trial_suite_isolation import attempt, with_profile


FAKE_DOCKER_PREFLIGHT = r'''
import json
from pathlib import Path
import re
import sys

config = json.loads(Path(__file__).with_suffix('.json').read_text())
original = sys.argv[1:]
args = original[:]
while args:
    if args[0] in ('--context', '--host', '-H', '--config'):
        del args[:2]
    elif any(args[0].startswith(flag + '=') for flag in ('--context', '--host', '-H', '--config')):
        del args[0]
    else:
        break
template = None
clean = []
while args:
    token = args.pop(0)
    if token in ('--format', '-f'):
        template = args.pop(0)
    elif token.startswith('--format='):
        template = token.split('=', 1)[1]
    elif token == '--type':
        args.pop(0)
    elif token.startswith('--type='):
        pass
    else:
        clean.append(token)
args = clean
operation = '.'.join(args[:2]) if args and args[0] in ('context', 'image', 'container', 'system') else (args[0] if args else '')
event = {'argv': original, 'operation': operation}
code, output, error = 0, '', ''
data = None
list_output = False
if args == ['--version']:
    output = 'Docker version ' + config['version']['Client']['Version'] + ', build synthetic'
elif args == ['context', 'show']:
    output = config['context']['Name']
elif args and args[0] == 'version':
    data = config['version']
elif args in (['info'], ['system', 'info']):
    data = config['info']
elif args[:2] == ['context', 'inspect']:
    data, list_output = config['context'], True
elif args[:2] == ['image', 'inspect'] or args[:1] == ['inspect']:
    event['operation'] = 'image.inspect'
    if config.get('image_missing'):
        code, error = 1, 'Error response from daemon: No such image: ' + config['image_ref']
    else:
        data, list_output = config['image'], True
elif config.get('lifecycle') and args[0] == 'create':
    options, labels, mounts = {}, {}, []
    index = 1
    while index < len(args):
        token = args[index]
        if token in ('--platform', '--cidfile', '--network', '--user', '--memory',
                     '--pids-limit', '--cpus', '--entrypoint', '--label', '--mount'):
            value = args[index + 1]
            if token == '--label':
                key, value = value.split('=', 1)
                labels[key] = value
            elif token == '--mount':
                fields = dict(item.split('=', 1) if '=' in item else (item, True) for item in value.split(','))
                mounts.append({'Type': fields['type'], 'Source': fields['source'],
                               'Destination': fields['target'], 'RW': not fields.get('readonly', False)})
            else:
                options[token] = value
            index += 2
        elif token.startswith('--'):
            index += 1
        else:
            break
    containers = config.setdefault('containers', {})
    identifier = format(len(containers) + 1, '064x')
    record = {
        'Id': identifier, 'Image': config['container_image'], 'Platform': config.get('container_os', 'linux'),
        'ImageManifestDescriptor': config['container_descriptor'],
        'Config': {'Image': args[index], 'Labels': labels, 'User': options['--user'],
                   'Entrypoint': [options['--entrypoint']], 'Healthcheck': {'Test': ['NONE']}},
        'HostConfig': {'ReadonlyRootfs': '--read-only' in args, 'Privileged': '--privileged' in args,
                       'PidMode': '', 'IpcMode': 'private', 'NetworkMode': options['--network'],
                       'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges'],
                       'RestartPolicy': {'Name': 'no'}, 'Memory': int(options['--memory']),
                       'PidsLimit': int(options['--pids-limit']), 'NanoCpus': int(float(options['--cpus']) * 1e9)},
        'Mounts': mounts, 'State': {'Running': False, 'Pid': 0, 'ExitCode': 0, 'Status': 'created'},
    }
    containers[identifier] = {'record': record, 'removed': False}
    cid = b'\xff' if config['lifecycle'] == 'malformed-cid' else identifier.encode()
    Path(options['--cidfile']).write_bytes(cid)
    output = identifier
    event['container_id'] = identifier
elif config.get('lifecycle') and args[:2] == ['container', 'inspect']:
    data, list_output = config['containers'][args[-1]]['record'], True
elif config.get('lifecycle') and args[0] == 'start':
    config['containers'][args[-1]]['record']['State']['Status'] = 'exited'
    output = '{}'  # Deliberately absent probe results; no fake qualification.
    if config['lifecycle'] in ('missing-interpreter', 'candidate-exit127'):
        missing = 'exec: "/usr/local/bin/python3": stat /usr/local/bin/python3: no such file or directory'
        state = config['containers'][args[-1]]['record']['State']
        if config['lifecycle'] == 'missing-interpreter':
            code, output, error = 1, '', 'Error response from daemon: unable to start container process: ' + missing
            state.update(Status='created', Error=missing)
        else:
            code, output, error = 127, '', missing
            state.update(ExitCode=127, Error='')
elif config.get('lifecycle') and args[0] == 'exec':
    code = 1
    error = ('Docker client could not execute request' if config['lifecycle'] == 'denial-diagnostic'
             else args[1] + ' is not running')
elif config.get('lifecycle') and args[0] == 'rm':
    config['containers'][args[-1]]['removed'] = True
    output = args[-1]
elif config.get('lifecycle') and args[:2] == ['container', 'ls']:
    identifier = next(value[3:] for value in args if value.startswith('id='))
    output = '' if config['containers'][identifier]['removed'] else identifier
    template = None
else:
    code, error = 90, 'FAKE-CLI-FORBIDDEN: no containers or providers may be started'
    event['forbidden'] = True

if data is not None:
    if template in ('json', '{{json .}}', '{{ json . }}'):
        output = json.dumps(data)
    elif template:
        pattern = r'\{\{\s*(json\s+)?\.([A-Za-z0-9_.]*)\s*\}\}'
        def field(match):
            value = data
            for key in filter(None, match.group(2).split('.')):
                value = value[key]
            return json.dumps(value) if match.group(1) or isinstance(value, (dict, list, bool)) else str(value)
        try:
            output = re.sub(pattern, field, template)
            if '{{' in output or '}}' in output:
                raise ValueError('unsupported Go template')
        except (KeyError, TypeError, ValueError):
            code, error = 91, 'FAKE-CLI-UNSUPPORTED-FORMAT: ' + template
            event['unsupported'] = True
    elif list_output:
        output = json.dumps([data])
    else:
        code, error = 91, 'FAKE-CLI-UNSUPPORTED-FORMAT: request documented JSON output'
        event['unsupported'] = True
event['returncode'] = code
Path(__file__).with_suffix('.json').write_text(json.dumps(config))
with Path(config['log']).open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(event) + '\n')
if output:
    print(output)
if error:
    print(error, file=sys.stderr)
raise SystemExit(code)
'''


@pytest.fixture
def docker_preflight(attempt):
    if os.name != "posix":
        pytest.skip("This fake CLI uses an explicit POSIX launcher; no Windows lifecycle claim")
    with_profile(attempt)
    root, suite = attempt["suite_root"], attempt["suite"]
    helper = root / "fake_docker_preflight.py"
    helper.write_text(FAKE_DOCKER_PREFLIGHT, encoding="utf-8")
    executable = root / "fake-docker"
    executable.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper))} \"$@\"\n")
    executable.chmod(0o755)
    profile_path = root / "profile.json"
    profile = json.loads(profile_path.read_text())
    profile.update(docker_executable=str(executable.resolve()), server_id="fixture-server-id",
                   server_version="29.4.3", runtime_paths=["/usr/local/bin/python3"])
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    image_ref = profile["image_digest"]
    config = {
        "log": str(root / "fake-docker-calls.jsonl"), "image_ref": image_ref,
        "version": {"Client": {"Version": "29.4.3", "ApiVersion": "1.53", "Os": "linux", "Arch": "amd64"},
                    "Server": {"Version": profile["server_version"], "ApiVersion": "1.53", "Os": "linux", "Arch": "amd64"}},
        "context": {"Name": profile["context"], "Metadata": {},
                    "Endpoints": {"docker": {"Host": profile["endpoint"], "SkipTLSVerify": False}},
                    "TLSMaterial": {}, "Storage": {}},
        "info": {"ID": profile["server_id"], "ServerVersion": profile["server_version"],
                 "OSType": "linux", "Architecture": "x86_64"},
        "image": {"Id": "sha256:" + "9" * 64, "RepoDigests": [image_ref], "RepoTags": [],
                  "Os": "linux", "Architecture": "amd64", "Variant": "",
                  "Config": {"Env": ["PATH=/usr/local/bin:/usr/bin:/bin"], "User": "1000:1000"}},
    }
    config_path = helper.with_suffix(".json")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    layout = derive_layout(suite, root, "normalize", "alpha", attempt["roots"], role="writer")
    return {**attempt, "config": config, "config_path": config_path, "layout": layout}


def call_api(shape, api):
    args = (shape["suite"], shape["suite_root"], shape["layout"])
    if api == "qualify":
        return qualify_profile(*args, shape["evidence"], roots=shape["roots"])
    return execute_isolated(*args, ["/usr/local/bin/python3", "-c", "raise SystemExit(99)"],
                            roots=shape["roots"], cwd="/workspace", env={}, prompt=None,
                            timeout=2, output_limit=1024, evidence_dir=shape["evidence"])


def cli_calls(shape):
    path = Path(shape["config"]["log"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("api", ["qualify", "execute"])
def test_consistent_fake_metadata_reaches_only_the_deliberately_rejected_probe_boundary(docker_preflight, api):
    shape = docker_preflight
    try:
        outcome = call_api(shape, api)
    except ValueError as exc:
        assert str(exc).startswith(("unsupported-profile:", "isolation-unqualified:"))
    else:
        assert api == "qualify", "Unqualified execution must not return a writer outcome"
        assert outcome["schema"] == "coding-trial-qualification/v1"
        assert outcome["producer"]["purpose"] == "synthetic"
        assert any(check["status"] == "failed" for check in outcome["checks"])
    calls = cli_calls(shape)
    assert calls and not any(row.get("unsupported") for row in calls), calls
    rejected = {row["operation"] for row in calls if row.get("forbidden")}
    assert rejected & {"create", "container.create", "run", "container.run", "exec", "container.exec"}
    # Creation always failed without an ID: there is no owned container to remove.
    assert rejected <= {"create", "container.create", "run", "container.run", "exec", "container.exec"}
    assert shape["evidence"].is_dir()


@pytest.mark.parametrize("api", ["qualify", "execute"])
@pytest.mark.parametrize("mismatch", ["context-name", "endpoint", "server-id", "server-version",
                                       "image-platform", "image-digest", "image-missing"])
def test_live_preflight_rejects_stale_identity_before_any_container_or_pull(docker_preflight, api, mismatch):
    shape, marker, operation = docker_preflight, "wrong-identity", ""
    config = shape["config"]
    if mismatch == "context-name":
        config["context"]["Name"], operation = marker, "context.inspect"
    elif mismatch == "endpoint":
        config["context"]["Endpoints"]["docker"]["Host"] = "unix:///wrong-identity.sock"
        operation = "context.inspect"
    elif mismatch == "server-id":
        config["info"]["ID"], operation = marker, "info"
    elif mismatch == "server-version":
        marker = "28.0.1"
        config["version"]["Server"]["Version"] = marker
        config["info"]["ServerVersion"] = marker
        operation = "server-version"
    elif mismatch == "image-platform":
        marker, operation = "arm64", "image.inspect"
        config["image"]["Architecture"] = marker
    elif mismatch == "image-digest":
        marker, operation = "b" * 64, "image.inspect"
        config["image"]["RepoDigests"] = ["example.invalid/wrong@sha256:" + marker]
    else:
        marker, operation = "No such image", "image.inspect"
        config["image_missing"] = True
    shape["config_path"].write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="^unsupported-profile:"):
        call_api(shape, api)
    calls = cli_calls(shape)
    assert calls and not any(row.get("forbidden") or row.get("unsupported") for row in calls), calls
    observed = {row["operation"] for row in calls}
    if operation == "server-version":
        assert observed & {"version", "info", "system.info"}
    elif operation == "info":
        assert observed & {"info", "system.info"}
    else:
        assert operation in observed
    assert shape["evidence"].is_dir()
    diagnostics = "\n".join(path.read_text(errors="replace")
                            for path in shape["evidence"].rglob("*") if path.is_file())
    assert marker in diagnostics  # Preserve the observed failure, not merely a claimed false gate.


@pytest.mark.parametrize("api", ["qualify", "execute"])
@pytest.mark.parametrize("failure", ["tampered-layout", "existing-evidence"])
def test_pure_rejection_never_invokes_even_the_fake_daemon(docker_preflight, api, failure):
    shape = docker_preflight
    if failure == "tampered-layout":
        shape["layout"] = deepcopy(shape["layout"])
        shape["layout"]["mounts"].append({"source": shape["roots"]["private"][0],
                                          "target": "/private-leak", "read_only": True})
        code = "suite-invalid"
    else:
        shape["evidence"].mkdir()
        (shape["evidence"] / "user.txt").write_text("preserve me")
        code = "input-exists"
    with pytest.raises(ValueError, match=f"^{code}:"):
        call_api(shape, api)
    assert cli_calls(shape) == []
    if failure == "tampered-layout":
        assert not shape["evidence"].exists()
    else:
        assert list(shape["evidence"].iterdir()) == [shape["evidence"] / "user.txt"]
        assert (shape["evidence"] / "user.txt").read_text() == "preserve me"


@pytest.fixture
def docker_lifecycle(docker_preflight):
    shape = docker_preflight
    config = shape["config"]
    # Actual Docker Desktop readback distinguished requested index d50f... from
    # selected platform manifest 72d3...; their identities must not be conflated.
    descriptor = {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                  "digest": config["image"]["Id"], "platform": {"os": "linux", "architecture": "amd64"}}
    config["image"]["Descriptor"] = deepcopy(descriptor)
    config.update(lifecycle="normal", container_image=config["image_ref"].split("@", 1)[1],
                  container_descriptor=deepcopy(descriptor))
    return shape


def failed_synthetic_qualification(shape):
    shape["config_path"].write_text(json.dumps(shape["config"]))
    result = call_api(shape, "qualify")
    assert result["schema"] == "coding-trial-qualification/v1"
    assert result["producer"]["purpose"] == "synthetic"
    assert any(row["status"] == "failed" for row in result["checks"])
    calls = cli_calls(shape)
    assert calls and not any(row.get("forbidden") or row.get("unsupported") for row in calls), calls
    assert not any(row["operation"] in {"pull", "build", "run"} for row in calls)
    return result, calls


@pytest.mark.parametrize("readback", ["index", "selected-manifest"])
def test_index_image_with_matching_selected_descriptor_reaches_probe(docker_lifecycle, readback):
    if readback == "selected-manifest":
        docker_lifecycle["config"]["container_image"] = docker_lifecycle["config"]["image"]["Id"]
    _, calls = failed_synthetic_qualification(docker_lifecycle)
    assert any(row["operation"] == "start" for row in calls), "Matching index/manifest/platform was rejected before probe"


@pytest.mark.parametrize("mismatch", ["index", "manifest", "architecture", "os"])
@pytest.mark.parametrize("readback", ["index", "selected-manifest"])
def test_container_image_descriptor_rejects_swapped_identity_before_probe(docker_lifecycle, mismatch, readback):
    config = docker_lifecycle["config"]
    if readback == "selected-manifest":
        config["container_image"] = config["image"]["Id"]
    if mismatch == "index":
        config["container_image"] = "sha256:" + "b" * 64
    elif mismatch == "manifest":
        config["container_descriptor"]["digest"] = "sha256:" + "b" * 64
    else:
        config["container_descriptor"]["platform"][mismatch] = "arm64" if mismatch == "architecture" else "windows"
    _, calls = failed_synthetic_qualification(docker_lifecycle)
    assert any(row["operation"] == "create" for row in calls)
    assert not any(row["operation"] == "start" for row in calls), "Mismatched actual image was executed"


@pytest.mark.parametrize("failure", ["normal", "malformed-cid", "denial-diagnostic"])
def test_owned_cleanup_survives_cid_and_denial_diagnostic_failures(docker_lifecycle, failure):
    shape = docker_lifecycle
    # Keep the previously supported direct-ID readback so cleanup regressions
    # remain independently reproducible before the distinct index fix lands.
    shape["config"].update(lifecycle=failure, container_image=shape["config"]["image"]["Id"])
    _, calls = failed_synthetic_qualification(shape)
    created = {row["container_id"] for row in calls if row["operation"] == "create"}
    assert created
    removed = {row["argv"][-1] for row in calls if row["operation"] == "rm"}
    assert removed == created, "Every exact owned stopped container must be removed even after a failed diagnostic"
    for identifier in created:
        assert any(row["operation"] == "container.inspect" and row["argv"][-1] == identifier for row in calls)
        assert any(row["operation"] == "container.ls" and "id=" + identifier in row["argv"] for row in calls)
    lifecycles = [json.loads(path.read_text()) for path in shape["evidence"].rglob("lifecycle.json")]
    assert {row["container_id"] for row in lifecycles} == created
    if failure == "denial-diagnostic":
        assert all(row["status"] == "failed" for row in lifecycles), "Successful removal cannot erase the failed denial control"
        diagnostics = "\n".join(path.read_text(errors="replace") for path in shape["evidence"].rglob("*.json"))
        assert "Docker client could not execute request" in diagnostics
    elif failure == "malformed-cid":
        assert any(path.read_bytes() == b"\xff" for path in shape["evidence"].rglob("*.cid")), "Retain malformed CID evidence"


def test_failed_qualification_binds_an_actual_retained_metadata_invocation(docker_lifecycle):
    shape = docker_lifecycle
    # Host client and container/server identities deliberately differ. The fake
    # CLI really emits metadata and returns an empty failed probe; no probe can pass.
    shape["config"]["version"]["Client"].update(Version="29.4.2", Os="darwin", Arch="arm64")
    shape["config"]["container_image"] = shape["config"]["image"]["Id"]
    shape["config_path"].write_text(json.dumps(shape["config"]))
    result = call_api(shape, "qualify")
    assert result["producer"]["purpose"] == "synthetic"
    assert any(row["status"] == "failed" for row in result["checks"])
    expected = []
    observed = [row["argv"] for row in cli_calls(shape) if row["operation"] == "version"]
    for path in shape["evidence"].rglob("*.json"):
        row = json.loads(path.read_text())
        if not isinstance(row, dict) or not isinstance(row.get("argv"), list) or "version" not in row["argv"]:
            continue
        process = row["process"]
        assert "--config" in row["argv"] and row["argv"][1:] in observed
        assert process["returncode"] == 0 and not process["stdout_truncated"]
        client = json.loads(process["stdout"])["Client"]
        expected.append({"argv": row["argv"], "returncode": process["returncode"],
                         "started_at": row["started_at"], "ended_at": row["ended_at"],
                         "executable_sha256": hashlib.sha256(Path(row["argv"][0]).read_bytes()).hexdigest(),
                         "version": client["Version"], "platform": client["Os"] + "/" + client["Arch"]})
    assert expected, "No retained actual metadata invocation"
    assert result["execution"] in expected, "Qualification must reuse an actual invocation, even when its checks fail"


def assert_every_owned_container_removed(shape, calls):
    created = {row["container_id"] for row in calls if row["operation"] == "create"}
    assert created
    assert {row["argv"][-1] for row in calls if row["operation"] == "rm"} == created
    for identifier in created:
        assert any(row["operation"] == "container.inspect" and row["argv"][-1] == identifier for row in calls)
        assert any(row["operation"] == "container.ls" and "id=" + identifier in row["argv"] for row in calls)
    lifecycles = [json.loads(path.read_text()) for path in shape["evidence"].rglob("lifecycle.json")]
    assert {row["container_id"] for row in lifecycles} == created
    return lifecycles


@pytest.mark.parametrize("api", ["qualify", "execute"])
def test_missing_fixed_image_interpreter_is_unsupported_and_owned_cleanup_runs(docker_lifecycle, api):
    shape = docker_lifecycle
    shape["config"].update(lifecycle="missing-interpreter", container_image=shape["config"]["image"]["Id"])
    shape["config_path"].write_text(json.dumps(shape["config"]))
    error = None
    try:
        call_api(shape, api)
    except ValueError as exc:
        error = exc
    calls = cli_calls(shape)
    assert_every_owned_container_removed(shape, calls)
    diagnostics = "\n".join(path.read_text(errors="replace") for path in shape["evidence"].rglob("*.json"))
    assert 'stat /usr/local/bin/python3: no such file or directory' in diagnostics
    assert not any(row.get("forbidden") or row["operation"] in {"pull", "build", "run"} for row in calls)
    assert error is not None and str(error).startswith("unsupported-profile:"), "Missing fixed runtime must not be reported as ordinary probe failure"


def test_candidate_exit127_and_error_text_are_not_fixed_runtime_absence(docker_lifecycle):
    shape = docker_lifecycle
    shape["config"].update(lifecycle="candidate-exit127", container_image=shape["config"]["image"]["Id"])
    _, calls = failed_synthetic_qualification(shape)
    assert_every_owned_container_removed(shape, calls)


@pytest.mark.parametrize("fault", ["denial-timeout", "denial-truncated", "absence-timeout", "absence-termination"])
def test_incomplete_decisive_evidence_fails_without_skipping_owned_removal(docker_lifecycle, monkeypatch, fault):
    import coding_trial_docker as docker
    shape = docker_lifecycle
    shape["config"]["container_image"] = shape["config"]["image"]["Id"]
    invoke, injected = docker.invoke, []
    def incomplete(state, args, **options):
        actual = invoke(state, args, **options)
        targeted = args[:1] == ["exec"] if fault.startswith("denial-") else args[:2] == ["container", "ls"]
        if not targeted:
            return actual
        result = deepcopy(actual)
        field = "timed_out" if fault.endswith("timeout") else "stderr_truncated" if fault.endswith("truncated") else "termination_error"
        result[field] = "synthetic termination failure" if field == "termination_error" else True
        injected.append({"purpose": "synthetic fault injection", "argv": args, "actual": actual, "injected": result})
        return result
    monkeypatch.setattr(docker, "invoke", incomplete)
    _, calls = failed_synthetic_qualification(shape)
    (shape["evidence"] / "synthetic-result-faults.json").write_text(json.dumps(injected, indent=2))
    assert injected, "The intended decisive boundary was not reached"
    lifecycles = assert_every_owned_container_removed(shape, calls)
    assert all(row["status"] == "failed" for row in lifecycles), "Incomplete denial/absence evidence cannot establish verified teardown"
