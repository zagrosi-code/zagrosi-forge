"""Fixed native recipes and observations; no execution or loading admission."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import stat

from coding_trial_inventory import fingerprint, inventory, relative_path
from coding_trial_manifest import validate_suite
from coding_trial_qualification import environment, fields, parse_json, read_bytes, require, resource, sha, text

ENTRY_READER = "from pathlib import Path; import sys; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())"
NATIVE_VERSION = "codex-cli 0.154.0"
FIXED_ENV = {"HOME": "/home/forge", "CODEX_HOME": "/codex", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}


def _config(suite, arm_id, runtime):
    host, arm = suite["host"], suite["arms"][arm_id]
    require(host["adapter"] == "codex" and host["version"] == NATIVE_VERSION, "Unsupported native host version")
    require(host["isolation"] and host["isolation"]["adapter"] == "docker-v1", "Native execution requires Docker")
    text(host["model"], "Native model")
    text(host["effort"], "Native effort")
    capabilities = host["capabilities"]
    require(type(capabilities["subagents"]) is bool and type(capabilities["tools"]) is list
            and all(tool == "shell" for tool in capabilities["tools"]), "Unsupported native capability")
    enabled = str(capabilities["subagents"]).lower()
    product = arm["product"] is not None
    settings = ['approval_policy="never"', 'approvals_reviewer="user"',
                "model_reasoning_effort=" + json.dumps(host["effort"], ensure_ascii=False),
                'cli_auth_credentials_store="file"', "features.plugins=" + str(product).lower(),
                "features.remote_plugin=false", "features.recommended_plugins=false", "agents.enabled=" + enabled,
                "features.multi_agent=" + enabled, "features.multi_agent_v2=false",
                'projects={"/workspace"={trust_level="untrusted"}}']
    if product:
        identifier = runtime["installed"]["plugin_id"]
        text(identifier, "Installed plugin ID")
        settings += ['marketplaces.forge-evaluator.source_type="local"',
                     'marketplaces.forge-evaluator.source="/marketplace"',
                     "plugins={" + json.dumps(identifier, ensure_ascii=False) + "={enabled=true}}"]
    else:
        require(runtime["installed"] is None, "Plain arm cannot enable a plugin")
    return [value for setting in settings for value in ("-c", setting)]


def native_recipe(suite, arm_id, runtime):
    """Recompute the fixed native command; arm configuration remains opaque data."""
    try:
        host = suite["host"]
        config = _config(suite, arm_id, runtime)
        environment(host["environment"], "Native environment")
        require(all(host["environment"].get(key, value) == value for key, value in FIXED_ENV.items()),
                "Native environment conflicts with a fixed value")
        return {"argv": [host["executable"], "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                         "--json", "--strict-config", "--color", "never", "--sandbox", "danger-full-access",
                         "--cd", "/workspace", "--model", host["model"], *config, "-"],
                "environment": host["environment"] | FIXED_ENV}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"loading-unqualified: {exc}") from exc


def _directory(value):
    require(isinstance(value, str), "Native root must be a path string")
    path = Path(value)
    require(path.is_absolute() and path == path.resolve(strict=True) and path.is_dir(),
            "Native root must be a resolved directory")
    return path


def _runtime_roots(roots, credentials, auth_file, suite_root):
    fields(roots, "workspace product public generated native_runtime private", "Native roots")
    paths = {key: _directory(roots[key]) for key in ("workspace", "product", "public", "generated", "native_runtime")}
    native = paths["native_runtime"]
    require(native.name == "native-runtime", "Unexpected native runtime locator")
    require(all(paths[key] == native.parent / key for key in ("workspace", "product", "public", "generated")),
            "Native roots must share the reserved attempt layout")
    require(type(roots["private"]) is list and roots["private"], "Private roots are required")
    private = [_directory(value) for value in roots["private"]]
    control = native.parent / "private"
    require(control in private, "Native preparation control root is missing")
    public = list(paths.values())
    require(not any(left.is_relative_to(right) or right.is_relative_to(left) for left in public for right in private),
            "Native roots overlap private material")
    for name in ("home", "codex", "plugins", "marketplace"):
        _directory(str(native / name))
    require({item.name for item in native.iterdir()} == {"runtime.json", "home", "codex", "plugins", "marketplace"},
            "Unexpected native runtime material")
    require(not any((native / "home").iterdir()), "Native home must start empty")
    _directory(str(native / "codex/plugins"))
    require(not any((native / "codex/plugins").iterdir()), "Native plugin mountpoint must start empty")
    expected = {"plugins"}
    if credentials == "codex-native-auth":
        require(auth_file is not None, "Native credentials are unavailable")
        auth = Path(auth_file)
        require(auth.is_absolute() and auth == auth.resolve(strict=True), "Credential path must not use aliases")
        info = auth.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Credential source must be a regular file")
        require(not any(auth.is_relative_to(root) for root in [*public, *private, suite_root]),
                "Credential source overlaps evaluator or candidate material")
        placeholder = native / "codex/auth.json"
        info = placeholder.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == 0,
                "Credential mountpoint must be an empty regular file")
        expected.add("auth.json")
    else:
        require(credentials is None and auth_file is None, "Undeclared native credentials")
    require({item.name for item in (native / "codex").iterdir()} == expected, "Native state must start clean")
    return paths, control


def _regular_bytes(root, name, *, max_bytes=None):
    path = root / name
    require(path == path.resolve(strict=True) and stat.S_ISREG(path.lstat().st_mode),
            f"Expected a regular native resource: {name}")
    return read_bytes(root, name, max_bytes=max_bytes)


def _native_number(value):
    number = float(value)
    require(math.isfinite(number), "Nonfinite native JSON number")
    return number


def _package_identity(product):
    root_manifest = product / "plugin.json"
    if root_manifest.exists() or root_manifest.is_symlink():
        raw = _regular_bytes(product, "plugin.json")
        try:
            # Native format discovery permits unrelated invalid JSON and uses the last duplicate key.
            value = json.loads(raw.decode("utf-8"), parse_int=_native_number, parse_float=_native_number,
                               parse_constant=_native_number)
        except (UnicodeError, ValueError):
            value = None
        schema = value.get("$schema") if type(value) is dict else None
        require(not isinstance(schema, str) or not schema.startswith("https://agent-plugins.org/schemas/"),
                "Unsupported overriding native manifest format")
    _directory(str(product / ".codex-plugin"))
    manifest = parse_json(_regular_bytes(product, ".codex-plugin/plugin.json"))
    require(type(manifest) is dict, "Native manifest must be an object")
    name = manifest.get("name")
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            and not name.startswith(".") and not name.endswith(".") and ".." not in name,
            "Native manifest needs an explicit valid name")
    version = manifest.get("version")
    require(version is None or isinstance(version, str), "Invalid native manifest version")
    version = (version or "").strip() or "local"
    require(version not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9._+-]+", version), "Invalid native version")
    return name, version


def _payload_tree(root, prefix, expected):
    """Verify one copied payload and permit only its structural ancestors."""
    target = _directory(str(root / prefix))
    require(inventory(target) == expected, "Installed or catalog payload differs from the frozen product")
    return {prefix, *(str(parent) for parent in PurePosixPath(prefix).parents if str(parent) != "."),
            *(prefix + "/" + name for name in expected)}


def _installed_payload(runtime, native, product, entries):
    plugins, marketplace = inventory(native / "plugins"), inventory(native / "marketplace")
    require(fingerprint(plugins) == runtime["plugins_sha256"]
            and fingerprint(marketplace) == runtime["marketplace_sha256"], "Native cache or catalog digest changed")
    if runtime["installed"] is None:
        require(not entries and not plugins and not marketplace and runtime["selected_entry"] is None,
                "Plain native runtime contains product material")
        return None
    name, version = _package_identity(product)
    identifier = name + "@forge-evaluator"
    relative = f"cache/forge-evaluator/{name}/{version}"
    require(runtime["installed"] == {"plugin_id": identifier, "version": version,
                                    "path": "/codex/plugins/" + relative, "inventory_sha256": fingerprint(entries)},
            "Installed identity differs from the native product")
    require(set(plugins) == _payload_tree(native / "plugins", relative, entries), "Unexpected cached plugin material")
    allowed = _payload_tree(native / "marketplace", "payload", entries)
    allowed.update({".agents", ".agents/plugins", ".agents/plugins/marketplace.json"})
    require(set(marketplace) == allowed, "Unexpected marketplace material")
    catalog = parse_json(_regular_bytes(native / "marketplace", ".agents/plugins/marketplace.json"))
    require(catalog == {"name": "forge-evaluator", "plugins": [{"name": name,
            "source": {"source": "local", "path": "./payload"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"}, "category": "Productivity"}]},
            "Native marketplace must select the frozen local product")
    selected = runtime["selected_entry"]
    relative_path(selected)
    installed = native / "plugins" / relative
    resource(product, selected)
    require(read_bytes(product, selected) == read_bytes(installed, selected), "Installed entry differs from product")
    return {"pluginId": identifier, "name": name, "marketplaceName": "forge-evaluator", "version": version,
            "installedPath": runtime["installed"]["path"], "authPolicy": "ON_USE"}


def _preparation_commands(runtime, suite, arm_id, added):
    """One fixed offline sequence shared by creation and prepared readback."""
    host = suite["host"]["executable"]
    commands, observations = [[host, "--version"]], [None]
    listing = {"installed": [], "available": []}
    if added is not None:
        prefix = [host, "-c", "features.plugins=true", "-c", "features.remote_plugin=false",
                  "-c", "features.recommended_plugins=false"]
        commands += [prefix + ["plugin", "marketplace", "add", "/marketplace", "--json"],
                     prefix + ["plugin", "add", added["pluginId"], "--json"]]
        observations += [{"marketplaceName": "forge-evaluator", "installedRoot": "/marketplace", "alreadyAdded": False}, added]
        listed = {key: value for key, value in added.items() if key != "installedPath"}
        listed.update(installed=True, enabled=True, source={"source": "local", "path": "/marketplace/payload"},
                      installPolicy="AVAILABLE")
        listing["installed"].append(listed)
    selected = [] if added is None else ["--marketplace", "forge-evaluator"]
    commands.append([host, *_config(suite, arm_id, runtime), "plugin", "list", *selected, "--json"])
    observations.append(listing)
    return commands, observations


def _preparation_observation(execution, command, expected, *, discovery=False):
    """Validate one actual readback before the next offline command can run."""
    fields(execution, "argv returncode stdout stderr", "Native preparation execution")
    require(execution["argv"] == command and type(execution["returncode"]) is int
            and execution["returncode"] == 0, "Native preparation command or outcome differs")
    require(isinstance(execution["stdout"], str) and isinstance(execution["stderr"], str), "Invalid preparation streams")
    if expected is None:
        require(execution["stdout"].strip() == NATIVE_VERSION, "Native version readback differs")
        return
    observed = parse_json(execution["stdout"])
    if discovery and type(observed) is dict:
        installed = observed.get("installed")
        if (type(installed) is list and len(installed) == 1 and type(installed[0]) is dict
                and "marketplaceSource" in installed[0]):
            source = installed[0].pop("marketplaceSource")
            require(source == {"sourceType": "local", "source": "/marketplace"},
                    "Native marketplace readback differs")
    require(fingerprint(observed) == fingerprint(expected), "Native preparation readback differs")


def _preparation(runtime, suite, arm_id, control, added):
    fields(runtime["preparation"], "path sha256", "Native preparation reference")
    reference = runtime["preparation"]
    require(reference["path"] == "native-preparation.json", "Unexpected native preparation locator")
    sha(reference["sha256"])
    raw = _regular_bytes(control, reference["path"])
    require(hashlib.sha256(raw).hexdigest() == reference["sha256"], "Native preparation bytes changed")
    record = parse_json(raw)
    fields(record, "schema image_digest executions", "Native preparation")
    require(record["schema"] == "coding-trial-native-preparation/v1"
            and record["image_digest"] == runtime["image_digest"], "Wrong native preparation identity")
    commands, observations = _preparation_commands(runtime, suite, arm_id, added)
    require(type(record["executions"]) is list and len(record["executions"]) == len(commands),
            "Wrong native preparation sequence")
    for index, (execution, command, expected) in enumerate(zip(record["executions"], commands, observations)):
        _preparation_observation(execution, command, expected,
                                 discovery=index == len(commands) - 1 and added is not None)


def read_native_runtime(suite, suite_root, task_id, arm_id, roots, *, auth_file=None):
    """Validate prepared bytes without reading credentials or claiming live loading."""
    try:
        root = Path(suite_root).resolve(strict=True)
        paths, control = _runtime_roots(roots, suite["host"]["credentials"], auth_file, root)
        suite = validate_suite(suite, suite_root)
        require(task_id in suite["tasks"], "Unknown native task")
        arm, host = suite["arms"][arm_id], suite["host"]
        native = paths["native_runtime"]
        runtime = parse_json(_regular_bytes(native, "runtime.json"))
        fields(runtime, "schema host_version host_executable host_executable_sha256 image_digest product_sha256 "
               "entry_sha256 configuration_sha256 capabilities_sha256 profile_sha256 plugins_sha256 marketplace_sha256 "
               "requirements_sha256 installed selected_entry launch preparation", "Native runtime")
        require(runtime["schema"] == "coding-trial-native-runtime/v1", "Unsupported native runtime schema")
        for key, value in runtime.items():
            if key.endswith("_sha256"):
                sha(value)
        profile_bytes = read_bytes(root, host["isolation"]["profile"])
        profile = parse_json(profile_bytes)
        require(runtime["host_version"] == host["version"] and runtime["host_executable"] == host["executable"]
                and runtime["image_digest"] == profile["image_digest"], "Native host or image identity differs")
        entries = inventory(paths["product"])
        expected = fingerprint({}) if arm["product"] is None else arm["product"]["inventory_sha256"]
        require(fingerprint(entries) == expected and runtime["product_sha256"] == expected,
                "Prepared native product differs")
        bindings = {"entry_sha256": hashlib.sha256(read_bytes(root, arm["entry"])).hexdigest(),
                    "configuration_sha256": fingerprint(arm["configuration"]),
                    "capabilities_sha256": fingerprint(host["capabilities"]),
                    "profile_sha256": hashlib.sha256(profile_bytes).hexdigest()}
        require(all(runtime[key] == value for key, value in bindings.items()), "Native runtime binding is stale")
        require((runtime["installed"] is None) == (arm["product"] is None), "Native product selection differs")
        if "selected_entry" in arm["loading"]:
            require(runtime["selected_entry"] == arm["loading"]["selected_entry"], "Declared native entry differs")
        added = _installed_payload(runtime, native, paths["product"], entries)
        require(runtime["launch"] == native_recipe(suite, arm_id, runtime), "Native launch settings differ")
        _preparation(runtime, suite, arm_id, control, added)
        return runtime
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"loading-unqualified: {exc}") from exc


def entry_read_command(runtime):
    """Quote the explicitly selected installed entry without guessing a skill."""
    installed, selected = runtime["installed"], runtime["selected_entry"]
    if installed is None and selected is None:
        return None
    try:
        relative_path(selected)
        path = installed["path"]
        require(isinstance(path, str) and path.startswith("/codex/plugins/cache/")
                and "\\" not in path and "\0" not in path
                and all(part not in {"", ".", ".."} for part in path.split("/")[1:]),
                "Invalid installed entry root")
        target = str(PurePosixPath(path) / selected)
        return shlex.join(["/usr/local/bin/python3", "-I", "-B", "-c", ENTRY_READER, target])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"loading-unqualified: {exc}") from exc


def _native_events(process):
    require(type(process) is dict and type(process.get("returncode")) is int
            and process["returncode"] == 0, "Native process did not succeed")
    require(all(process.get(key) is False for key in
                ("timed_out", "stdout_truncated", "stderr_truncated"))
            and process.get("termination_error") is None,
            "Native process evidence is incomplete")
    require(isinstance(process.get("stdout"), str) and isinstance(process.get("stderr"), str),
            "Native process streams must be text")
    events, kinds, completed = [], {}, set()
    for line in process["stdout"].splitlines():
        if not line.strip():
            continue
        event = parse_json(line)
        require(type(event) is dict, "Native event must be an object")
        kind = event.get("type")
        text(kind, "Native event type")
        require(kind not in {"error", "turn.failed"}, "Native stream reports a failure")
        if kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            require(type(item) is dict, "Native item must be an object")
            identifier, item_kind = item.get("id"), item.get("type")
            text(identifier, "Native item ID")
            text(item_kind, "Native item type")
            require(identifier not in completed and kinds.get(identifier, item_kind) == item_kind,
                    "Native item changed identity or repeated completion")
            kinds[identifier] = item_kind
            if kind == "item.completed":
                completed.add(identifier)
        elif kind == "thread.started":
            text(event.get("thread_id"), "Native thread ID")
        else:
            require(kind in {"turn.started", "turn.completed"}, "Unsupported native event type")
        events.append(event)
    return events


def _command_ids(items, command, expected=None, *, shell="/bin/sh"):
    if not isinstance(command, str) or not command:
        return []
    for item in items:
        if (item["type"] != "command_execution" or item.get("status") != "completed"
                or type(item.get("exit_code")) is not int or item["exit_code"] != 0
                or not isinstance(item.get("command"), str)
                or not isinstance(item.get("aggregated_output"), str)):
            continue
        try:
            matches = shlex.split(item["command"]) == [shell, "-c", command]
            if matches and (expected is None or item["aggregated_output"].encode("utf-8") == expected):
                return [item["id"]]
        except (ValueError, UnicodeError):
            continue
    return []


def _subagent_ids(events):
    seen, introduced, spawned = set(), {}, {}
    for event in events:
        if event["type"] == "thread.started":
            seen.add(event["thread_id"])
        if event["type"] not in {"item.started", "item.updated", "item.completed"}:
            continue
        item = event["item"]
        if item["type"] != "collab_tool_call":
            continue
        sender, receivers = item.get("sender_thread_id"), item.get("receiver_thread_ids")
        if (not isinstance(sender, str) or not sender or type(receivers) is not list
                or not receivers or any(not isinstance(child, str) or not child for child in receivers)
                or len(set(receivers)) != len(receivers) or sender in receivers):
            continue
        seen.add(sender)
        terminal = event["type"] == "item.completed" and item.get("status") == "completed"
        if item.get("tool") == "spawn_agent":
            for child in receivers:
                origin = (sender, item["id"])
                if child not in seen:
                    introduced[child] = origin
                if terminal and introduced.get(child) == origin:
                    spawned[child] = origin
        seen.update(receivers)
        if item.get("tool") == "wait" and terminal:
            states = item.get("agents_states")
            if type(states) is not dict:
                continue
            for child in receivers:
                state = states.get(child)
                if (child in spawned and spawned[child][0] == sender
                        and type(state) is dict and state.get("status") == "completed"):
                    return [spawned[child][1], item["id"]]
    return []


def _observation(ids, detail, *, applicable=True):
    return {"status": ("passed" if ids else "failed") if applicable else "not_applicable",
            "detail": detail, "event_ids": ids}


def check_native_events(process, *, entry_command, entry_bytes, action_command, subagents, action_bytes=None):
    """Read complete terminal observations; task effects and cleanup remain external."""
    try:
        require(type(subagents) is bool, "Subagent requirement must be a boolean")
        events = _native_events(process)
    except (TypeError, ValueError) as exc:
        return {name: _observation([], str(exc)) for name in
                ("entry-routing", "required-tools", "subagent-execution")}
    plain = entry_command is None and entry_bytes is None
    items = [event["item"] for event in events if event["type"] == "item.completed"]
    entry = _command_ids(items, entry_command, entry_bytes) if type(entry_bytes) is bytes else []
    action = _command_ids(items, action_command, action_bytes)
    action_detail = "Exact controller action command required"
    if (not action and type(action_bytes) is bytes
            and _command_ids(items, action_command, action_bytes, shell="/usr/bin/sh")):
        action_detail = ("Exact controller action and output observed under /usr/bin/sh -c; "
                         "/bin/sh -c required. This wrapper is not accepted.")
    children = _subagent_ids(events) if subagents else []
    child_detail = "Spawn and later completion of the same child required"
    if subagents and not children:
        child_detail = ("No qualifying spawn followed by completion of the same child was observed. "
                        "Empty waits or model claims do not establish completion.")
    return {
        "entry-routing": _observation(entry, "Exact installed-entry command and bytes required", applicable=not plain),
        "required-tools": _observation(action, action_detail),
        "subagent-execution": _observation(children, child_detail, applicable=subagents),
    }
