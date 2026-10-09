"""Strict, side-effect-free inputs for the opt-in coding trial suite."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path, PurePosixPath
import re

from coding_trial_inventory import contains_path, fingerprint, inventory, project, relative_path
from coding_trial_qualification import (
    require as _require,
    fields as _fields,
    text as _text,
    choice as _choice,
    integer as _integer,
    positive as _positive,
    boolean as _boolean,
    strings as _strings,
    sha as _sha,
    image as _image,
    environment as _environment,
    parse_json as _parse,
    resource as _resource,
    read_bytes as _bytes,
    validate_qualifications,
)


def _paths(value, where):
    _strings(value, where, unique=True)
    for name in value:
        relative_path(name)


def _identifier(value):
    _text(value, "identifier")
    _require(re.fullmatch(r"[a-z][a-z0-9-]*", value), f"Invalid identifier: {value}")


def _git_id(value):
    _require(isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value),
             "Git identities must be complete object IDs")


def _overlap(left, right):
    return contains_path(left, right) or contains_path(right, left)


def _command(value, root, baseline_root, role):
    _fields(value, "id entry support argv cwd env timeout_seconds output_bytes", role)
    _identifier(value["id"])
    _strings(value["argv"], "argv", nonempty=True)
    _paths(value["support"], "support")
    for name in value["support"]:
        _resource(root, name)
    if value["entry"] is not None:
        _resource(root, value["entry"])
    _require(role == "native" or value["entry"] is not None, f"{role} requires an entry")
    _environment(value["env"], "command environment")
    tokens = [*value["argv"], *value["env"].values()]
    _require(value["entry"] is not None or "{entry}" not in tokens, "Null entry cannot be expanded")
    _require(role == "oracle" or not set(tokens).intersection({"{assessor}", "{receipt}", "{assessment}"}),
             "Public commands cannot receive private paths")
    if role == "oracle":
        _require(not {"PYTHONPATH", "PYTHONHOME"}.intersection(value["env"]),
                 "Private oracle cannot override its clean Python environment")
    cwd_root = root if role == "oracle" else baseline_root
    if value["cwd"] != ".":
        _resource(cwd_root, value["cwd"], directory=True)
    _positive(value["timeout_seconds"], "timeout_seconds")
    _integer(value["output_bytes"], "output_bytes", minimum=1)
    _require(value["timeout_seconds"] <= 86400 and value["output_bytes"] <= 8388608,
             "Command bounds exceed the shared executor limits")


def _source(value, root):
    _fields(value, "kind url commit tree export preparation baseline_sha256", "source")
    _choice(value["kind"], "fixture git", "source kind")
    if value["kind"] == "fixture":
        _require(all(value[key] is None for key in ("url", "commit", "tree")),
                 "Fixture source has no upstream Git identity")
    else:
        _text(value["url"], "source URL")
        _git_id(value["commit"])
        _git_id(value["tree"])
    baseline_root = _resource(root, value["export"], directory=True)
    baseline = inventory(baseline_root)
    _sha(value["baseline_sha256"])
    _require(fingerprint(baseline) == value["baseline_sha256"], "Baseline inventory changed")
    preparation = value["preparation"]
    if preparation is not None:
        _fields(preparation, "description input_sha256 output_sha256 evidence", "preparation")
        _text(preparation["description"], "preparation description")
        _sha(preparation["input_sha256"])
        _sha(preparation["output_sha256"])
        _resource(root, preparation["evidence"])
        _require(preparation["output_sha256"] == value["baseline_sha256"], "Preparation output changed")
    return baseline_root, baseline


def _task(value, root):
    _fields(value, "source brief clarifications scope dependencies checks cleanup_required local_commits admission", "task")
    baseline_root, baseline = _source(value["source"], root)
    _bytes(root, value["brief"]).decode("utf-8")
    if value["clarifications"] is not None:
        _bytes(root, value["clarifications"]).decode("utf-8")
    _boolean(value["cleanup_required"], "cleanup_required")
    _choice(value["local_commits"], "allow forbid", "local_commits")
    scope = value["scope"]
    _fields(scope, "implementation tests config allowed_changes protected generated", "scope")
    for role, paths in scope.items():
        _paths(paths, role)
    assessed = scope["implementation"] + scope["tests"] + scope["config"]
    _require(assessed, "Task must declare assessed paths")
    project(baseline, tuple(assessed))  # Cross-role links cannot hide unassessed behavior.
    _require(all(any(contains_path(role, path) for role in assessed) for path in scope["allowed_changes"]),
             "Allowed changes must stay inside assessed roles")
    _require(not any(contains_path(path, name) for path in scope["generated"] for name in baseline),
             "Generated exclusions hide baseline material")
    _require(not any(_overlap(left, right) for left in scope["generated"] for right in scope["protected"]),
             "Generated paths overlap assessed or protected roles")
    dependencies = value["dependencies"]
    _fields(dependencies, "environment locks allow_lock_changes", "dependencies")
    _paths(dependencies["locks"], "locks")
    _boolean(dependencies["allow_lock_changes"], "allow_lock_changes")
    for name in dependencies["locks"]:
        _require(name in baseline and baseline[name]["type"] == "file", f"Missing regular lock: {name}")
    checks = value["checks"]
    _fields(checks, "feature_ids preservation_ids native oracle worker", "checks")
    ids = []
    for role in ("feature_ids", "preservation_ids"):
        _strings(checks[role], role, unique=True)
        for name in checks[role]:
            _identifier(name)
        ids.extend(checks[role])
    _require(ids and len(ids) == len(set(ids)), "Check IDs must be nonempty and disjoint")
    _require(type(checks["native"]) is list and checks["native"], "Native commands must be nonempty")
    for command in checks["native"]:
        _command(command, root, baseline_root, "native")
    for role in ("oracle", "worker"):
        _command(checks[role], root, baseline_root, role)
    command_ids = [command["id"] for command in [*checks["native"], checks["oracle"], checks["worker"]]]
    _require(len(command_ids) == len(set(command_ids)), "Command IDs must be unique")
    return baseline_root, baseline


def _arm(value, root, purpose):
    _fields(value, "product entry configuration workflow artifacts loading", "arm")
    _bytes(root, value["entry"]).decode("utf-8")
    _require(type(value["configuration"]) is dict, "Arm configuration must be an object")
    _choice(value["workflow"], "none forge-v1 unmeasured", "workflow")
    _paths(value["artifacts"], "artifacts")
    for index, left in enumerate(value["artifacts"]):
        _require(not any(_overlap(left, right) for right in value["artifacts"][index + 1:]),
                 "Workflow roots overlap")
    product = value["product"]
    if product is not None:
        _fields(product, "source_commit source_tree payload inventory_sha256", "product")
        for key in ("source_commit", "source_tree"):
            if purpose == "prospective" or product[key] is not None:
                _git_id(product[key])
        _require((product["source_commit"] is None) == (product["source_tree"] is None),
                 "Product Git identities must be paired")
        _sha(product["inventory_sha256"])
        _require(fingerprint(inventory(_resource(root, product["payload"], directory=True)))
                 == product["inventory_sha256"], "Product inventory changed")
    else:
        _require(value["workflow"] == "none" and not value["artifacts"], "Plain arm cannot claim a product workflow")
    loading = value["loading"]
    _fields(loading, "adapter receipt" + (" selected_entry" if type(loading) is dict
                                        and "selected_entry" in loading else ""), "loading")
    adapter = "none" if purpose == "synthetic" else "codex-plain-v1" if product is None else "codex-plugin-v1"
    _require(value["loading"]["adapter"] == adapter, "Loading adapter does not match arm and purpose")
    if purpose == "synthetic":
        _require(value["loading"]["receipt"] is None, "Fixture loading cannot qualify a native host")
    selected = loading.get("selected_entry")
    if selected is not None:
        _require(purpose == "prospective" and product is not None, "Only a native product may select an entry")
        _resource(_resource(root, product["payload"], directory=True), selected)


def _profile(value):
    _fields(value, "schema docker_executable context endpoint server_id server_version image_digest platform user limits network runtime_paths network_checks", "Docker profile")
    _require(value["schema"] == "coding-trial-docker-profile/v1", "Unsupported profile schema")
    for key in ("docker_executable", "context", "endpoint", "server_id", "server_version", "platform", "user"):
        _text(value[key], key)
    _require(Path(value["docker_executable"]).is_absolute(), "Docker executable must be absolute")
    _require(re.fullmatch(r"[0-9]+:[0-9]+", value["user"]), "Profile user must be UID:GID")
    _image(value["image_digest"])
    _fields(value["limits"], "memory_bytes pids cpus", "limits")
    for key in ("memory_bytes", "pids"):
        _integer(value["limits"][key], key, minimum=1)
    _positive(value["limits"]["cpus"], "cpus")
    _choice(value["network"], "none outbound-enabled", "network")
    expected = ["network-denied"] if value["network"] == "none" else ["network-mode-confirmed"]
    _require(value["network_checks"] == expected, "Network checks must match the profile")
    _strings(value["runtime_paths"], "runtime_paths", unique=True)
    _require(all(PurePosixPath(path).is_absolute() for path in value["runtime_paths"]),
             "Runtime paths must be absolute POSIX container paths")


def _host(value, root, purpose):
    _fields(value, "adapter executable version model effort isolation capabilities environment credentials fixture_argv", "host")
    _require(value["adapter"] == ("fixture" if purpose == "synthetic" else "codex"), "Host adapter does not match purpose")
    _text(value["executable"], "host executable")
    executable = Path(value["executable"]) if purpose == "synthetic" else PurePosixPath(value["executable"])
    _require(executable.is_absolute(), "Host executable must be absolute in its execution environment")
    _text(value["version"], "host version")
    _environment(value["environment"], "host environment")
    _fields(value["capabilities"], "tools subagents network", "capabilities")
    _strings(value["capabilities"]["tools"], "tools", unique=True)
    _boolean(value["capabilities"]["subagents"], "subagents")
    _choice(value["capabilities"]["network"], "none outbound-enabled", "network")
    if purpose == "synthetic":
        _require(all(value[key] is None for key in ("model", "effort", "credentials")), "Fixture cannot declare native identity or credentials")
        _strings(value["fixture_argv"], "fixture_argv", nonempty=True)
    else:
        _text(value["model"], "model")
        _text(value["effort"], "effort")
        _require(value["credentials"] in (None, "codex-native-auth"), "Unknown credential reference")
        _require(value["fixture_argv"] is None, "Native host cannot use fixture argv")
    isolation = value["isolation"]
    if isolation is None:
        return None
    _fields(isolation, "adapter profile image_digest probe_receipt", "isolation")
    _require(isolation["adapter"] == "docker-v1", "Unsupported isolation adapter")
    _image(isolation["image_digest"])
    profile = _parse(_bytes(root, isolation["profile"]))
    _profile(profile)
    _require(profile["image_digest"] == isolation["image_digest"], "Profile image differs from host")
    _require(profile["network"] == value["capabilities"]["network"], "Profile network differs from host")
    return profile


def _json_data(value):
    if type(value) is dict:
        for key, item in value.items():
            _text(key, "JSON key", empty=True)
            _json_data(item)
    elif type(value) is list:
        for item in value:
            _json_data(item)
    else:
        _require(type(value) in (str, int, float, bool, type(None)), "Value is not JSON data")
        _require(type(value) is not float or math.isfinite(value), "JSON numbers must be finite")


def _validate(data, root):
    _json_data(data)
    _fields(data, "schema id purpose tasks arms host repeats execution_seed blind_seed", "suite")
    _require(data["schema"] == "coding-trial-suite/v1", "Unsupported suite schema")
    _identifier(data["id"])
    _choice(data["purpose"], "synthetic prospective", "purpose")
    _integer(data["repeats"], "repeats", minimum=1)
    for name in ("execution_seed", "blind_seed"):
        _integer(data[name], name)
    root = Path(root).resolve(strict=True)
    _require(root.is_dir(), "Suite root must be a directory")
    for role in ("tasks", "arms"):
        _require(type(data[role]) is dict and data[role], f"{role} must be a nonempty object")
        for name in data[role]:
            _identifier(name)
    profile = _host(data["host"], root, data["purpose"])
    baselines = {name: _task(task, root) for name, task in data["tasks"].items()}
    for arm in data["arms"].values():
        _arm(arm, root, data["purpose"])
        for name, task in data["tasks"].items():
            scope = task["scope"]
            baseline = baselines[name][1]
            reserved = scope["implementation"] + scope["tests"] + scope["config"] + scope["protected"] + scope["generated"]
            _require(not any(_overlap(artifact, path) for artifact in arm["artifacts"] for path in reserved),
                     "Workflow paths overlap task roles")
            _require(not any(contains_path(artifact, path) for artifact in arm["artifacts"] for path in baseline),
                     "Workflow paths hide baseline material")
    validate_qualifications(data, root, baselines, profile)
    return deepcopy(data)


def validate_suite(data: dict, root: Path) -> dict:
    """Validate trusted input without mutating it, creating files or admitting a host."""
    try:
        return _validate(data, root)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError(f"suite-invalid: {exc}") from exc


def read_suite(path: Path) -> dict:
    """Read fresh JSON and resources on each call; persisted snapshots bind execution."""
    try:
        path = Path(path)
        return validate_suite(_parse(path.read_text(encoding="utf-8")), path.parent)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        if str(exc).startswith("suite-invalid:"):
            raise
        raise ValueError(f"suite-invalid: {exc}") from exc
