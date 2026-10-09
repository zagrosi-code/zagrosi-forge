"""Independent static native controls; fictional records never qualify a host."""
from __future__ import annotations

from copy import deepcopy
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_native import native_recipe, read_native_runtime
from trial_suite_fixtures import digest, link, make_suite, regular_entries, write_files


HOST = "/fixture/bin/codex"
VERSION = "codex-cli 0.154.0"
NAME = "neutral-product"
PLUGIN_ID = NAME + "@forge-evaluator"
SELECTED_ENTRY = "docs/review's entry.md"
REQUIREMENTS = ('[marketplaces]\nrestrict_to_allowed_sources = true\n\n'
                '[marketplaces.allowed_sources.evaluator]\nsource = "local"\npath = "/marketplace"\n')


def byte_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expected_config(*, product, subagents=False, effort="high"):
    # Literal contract expectations, independent of the production recipe.
    enabled = "true" if subagents else "false"
    result = ['approval_policy="never"', 'approvals_reviewer="user"',
              "model_reasoning_effort=" + json.dumps(effort, ensure_ascii=False),
              'cli_auth_credentials_store="file"',
              "features.plugins=" + ("true" if product else "false"),
              "features.remote_plugin=false", "features.recommended_plugins=false",
              "agents.enabled=" + enabled, "features.multi_agent=" + enabled,
              "features.multi_agent_v2=false", 'projects."/workspace".trust_level="untrusted"']
    if product:
        result += ['marketplaces.forge-evaluator.source_type="local"',
                   'marketplaces.forge-evaluator.source="/marketplace"',
                   'plugins."neutral-product@forge-evaluator".enabled=true']
    return [value for setting in result for value in ("-c", setting)]


def expected_recipe(*, product, subagents=False, environment=None, model="fictional-model", effort="high"):
    return {"argv": [HOST, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json",
                     "--strict-config", "--color", "never", "--sandbox", "danger-full-access", "--cd",
                     "/workspace", "--model", model,
                     *expected_config(product=product, subagents=subagents, effort=effort), "-"],
            "environment": (environment or {}) | {"HOME": "/home/forge", "CODEX_HOME": "/codex",
                                                 "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}}


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Static native helpers must not launch a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def make_native(tmp_path, *, plain=False, subagents=False, configuration=None,
                manifest_version=" 2.7.1 ", installed_version="2.7.1", root_manifest=None):
    path, suite = make_suite(tmp_path / "assessor")
    suite["purpose"] = "prospective"
    suite["arms"] = {name: suite["arms"][name] for name in ("alpha", "bravo")}
    arm_id = "alpha" if plain else "bravo"
    arm = suite["arms"][arm_id]
    for item in suite["arms"].values():
        if item["product"] is not None:
            item["product"].update(source_commit="b" * 40, source_tree="c" * 40)
        item["loading"] = {"adapter": "codex-plain-v1" if item["product"] is None else "codex-plugin-v1",
                           "receipt": None}
    if configuration is not None:
        arm["configuration"] = deepcopy(configuration)
    base = tmp_path / "attempt"
    native = base / "native-runtime"
    private = base / "private"
    for directory in (private, native / "home", native / "codex/plugins", native / "plugins", native / "marketplace",
                      base / "product", base / "public", base / "generated"):
        directory.mkdir(parents=True)
    shutil.copytree(path.parent / "export", base / "workspace")
    roots = {name: str((base / name).resolve()) for name in ("workspace", "product", "public", "generated")}
    # The preparation locator must not guess the first private root.
    roots.update(native_runtime=str(native.resolve()), private=[str(path.parent.resolve()), str(private.resolve())])
    image = "example.invalid/static-native-fixture@sha256:" + "a" * 64
    profile = {"schema": "coding-trial-docker-profile/v1", "docker_executable": str(tmp_path / "never-run-docker"),
               "context": "fictional", "endpoint": "unix:///never-used/docker.sock", "server_id": "fictional",
               "server_version": "fictional-v1", "image_digest": image, "platform": "linux/amd64",
               "user": "1000:1000", "limits": {"memory_bytes": 67108864, "pids": 32, "cpus": 0.5},
               "network": "none", "runtime_paths": [HOST, "/usr/local/bin/python3"],
               "network_checks": ["network-denied"]}
    save(path.parent / "profile.json", profile)
    suite["host"].update(adapter="codex", executable=HOST, version=VERSION, model="fictional-model", effort="high",
                         capabilities={"tools": ["shell"], "subagents": subagents, "network": "none"},
                         environment={"LANG": "C.UTF-8"}, credentials=None, fixture_argv=None,
                         isolation={"adapter": "docker-v1", "profile": "profile.json", "image_digest": image,
                                    "probe_receipt": None})
    installed = None
    plugin_names, marketplace_names = (), ()
    source = None
    if not plain:
        source = path.parent / "products/native"
        files = {".codex-plugin/plugin.json": json.dumps({"name": NAME, "version": manifest_version,
                                                        "description": "Opaque fixture metadata", "custom": {"keep": True}}),
                 SELECTED_ENTRY: "Read the selected neutral entry.\n",
                 "scripts/helper.py": "# Frozen product support, never executed.\n"}
        write_files(source, files)
        product_names = tuple(files)
        if root_manifest == "agent-schema":
            save(source / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/plugin.v1.json", "name": NAME})
            product_names += ("plugin.json",)
        elif root_manifest == "directory":
            (source / "plugin.json").mkdir()
            product_names += ("plugin.json",)
        elif root_manifest == "symlink":
            link(source / "plugin.json", ".codex-plugin/plugin.json")
        def expected_entries(root, names, *, prefix=""):
            entries = regular_entries(root, names)
            if root_manifest == "symlink":
                relative = prefix + "plugin.json"
                entries[relative] = {"type": "symlink", "mode": stat.S_IMODE((root / relative).lstat().st_mode),
                                     "target": ".codex-plugin/plugin.json"}
            return entries
        product_hash = digest(expected_entries(source, product_names))
        arm["product"] = {"source_commit": "b" * 40, "source_tree": "c" * 40,
                          "payload": "products/native", "inventory_sha256": product_hash}
        shutil.copytree(source, base / "product", dirs_exist_ok=True, symlinks=True)
        cache_relative = "cache/forge-evaluator/neutral-product/" + installed_version
        installed_path = "/codex/plugins/" + cache_relative
        shutil.copytree(source, native / "plugins" / cache_relative, symlinks=True)
        shutil.copytree(source, native / "marketplace/payload", symlinks=True)
        catalog = {"name": "forge-evaluator", "plugins": [{"name": NAME,
                   "source": {"source": "local", "path": "./payload"},
                   "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"}, "category": "Productivity"}]}
        save(native / "marketplace/.agents/plugins/marketplace.json", catalog)
        plugin_names = tuple(cache_relative + "/" + name for name in product_names)
        marketplace_names = (".agents/plugins/marketplace.json", *("payload/" + name for name in product_names))
        installed = {"plugin_id": PLUGIN_ID, "version": installed_version, "path": installed_path, "inventory_sha256": product_hash}
    plugins_hash = digest({}) if plain else digest(expected_entries(native / "plugins", plugin_names, prefix=cache_relative + "/"))
    marketplace_hash = digest({}) if plain else digest(expected_entries(native / "marketplace", marketplace_names, prefix="payload/"))
    runtime = {"schema": "coding-trial-native-runtime/v1", "host_version": VERSION, "host_executable": HOST,
               "host_executable_sha256": "d" * 64, "image_digest": image,
               "product_sha256": digest({}) if plain else installed["inventory_sha256"],
               "entry_sha256": byte_hash(path.parent / arm["entry"]),
               "configuration_sha256": digest(arm["configuration"]),
               "capabilities_sha256": digest(suite["host"]["capabilities"]),
               "profile_sha256": byte_hash(path.parent / "profile.json"),
               "plugins_sha256": plugins_hash, "marketplace_sha256": marketplace_hash,
               "requirements_sha256": hashlib.sha256(REQUIREMENTS.encode()).hexdigest(), "installed": installed,
               "selected_entry": None if plain else SELECTED_ENTRY,
               "launch": expected_recipe(product=not plain, subagents=subagents, environment={"LANG": "C.UTF-8"})}
    configs = expected_config(product=not plain, subagents=subagents)
    def execution(argv, stdout):
        return {"argv": argv, "returncode": 0, "stdout": stdout, "stderr": ""}
    executions = [execution([HOST, "--version"], VERSION + "\n")]
    listing = {"installed": [], "available": []}
    if not plain:
        install_prefix = [HOST, "-c", "features.plugins=true", "-c", "features.remote_plugin=false",
                          "-c", "features.recommended_plugins=false"]
        added = {"pluginId": PLUGIN_ID, "name": NAME, "marketplaceName": "forge-evaluator", "version": installed_version,
                 "installedPath": installed_path, "authPolicy": "ON_USE"}
        listing["installed"] = [{"pluginId": PLUGIN_ID, "name": NAME, "marketplaceName": "forge-evaluator",
                                 "version": installed_version, "installed": True, "enabled": True,
                                 "source": {"source": "local", "path": "/marketplace/payload"},
                                 "installPolicy": "AVAILABLE", "authPolicy": "ON_USE"}]
        executions += [execution(install_prefix + ["plugin", "marketplace", "add", "/marketplace", "--json"],
                                json.dumps({"marketplaceName": "forge-evaluator", "installedRoot": "/marketplace",
                                            "alreadyAdded": False})),
                       execution(install_prefix + ["plugin", "add", PLUGIN_ID, "--json"], json.dumps(added))]
    list_flags = [] if plain else ["--marketplace", "forge-evaluator"]
    executions.append(execution([HOST, *configs, "plugin", "list", *list_flags, "--json"], json.dumps(listing)))
    preparation = {"schema": "coding-trial-native-preparation/v1", "image_digest": image, "executions": executions}
    preparation_path = private / "native-preparation.json"
    save(preparation_path, preparation)
    runtime["preparation"] = {"path": "native-preparation.json", "sha256": byte_hash(preparation_path)}
    save(native / "runtime.json", runtime)
    save(path, suite)
    return {"suite": suite, "suite_root": path.parent, "arm": arm_id, "roots": roots, "native": native,
            "runtime": runtime, "preparation": preparation, "preparation_path": preparation_path,
            "source": source, "plugin_names": plugin_names, "marketplace_names": marketplace_names}


def read(fixture, *, auth_file=None):
    return read_native_runtime(fixture["suite"], fixture["suite_root"], "normalize", fixture["arm"], fixture["roots"],
                               auth_file=auth_file)


def persist(fixture, *, preparation=False):
    if preparation:
        save(fixture["preparation_path"], fixture["preparation"])
        fixture["runtime"]["preparation"]["sha256"] = byte_hash(fixture["preparation_path"])
    save(fixture["native"] / "runtime.json", fixture["runtime"])


@pytest.mark.parametrize("plain,subagents", [(False, False), (True, False), (False, True)])
def test_complete_static_runtime_is_detached_and_recipe_exact(tmp_path, plain, subagents):
    fixture = make_native(tmp_path, plain=plain, subagents=subagents)
    before = deepcopy(fixture["suite"])
    result = read(fixture)
    assert result == fixture["runtime"]
    assert native_recipe(fixture["suite"], fixture["arm"], result) == fixture["runtime"]["launch"]
    result["launch"]["argv"].append("must not persist")
    assert read(fixture) == fixture["runtime"]
    assert fixture["suite"] == before


def test_blank_manifest_version_uses_literal_local_cache_identity(tmp_path):
    fixture = make_native(tmp_path, manifest_version=" \t ", installed_version="local")
    result = read(fixture)
    assert result == fixture["runtime"]
    assert result["installed"]["version"] == "local"
    assert result["installed"]["path"] == "/codex/plugins/cache/forge-evaluator/neutral-product/local"


@pytest.mark.parametrize("kind", ["agent-schema", "directory", "symlink"])
def test_overriding_or_nonregular_root_manifest_cannot_use_codex_fallback(tmp_path, kind):
    # All copies and inventories consistently include the root manifest; this
    # distinguishes native format precedence from a mere stale digest.
    fixture = make_native(tmp_path, root_manifest=kind)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture)


def test_arbitrary_product_configuration_is_bound_data_never_cli_overrides(tmp_path):
    configuration = {"provider": {"name": "opaque", "options": [True, None, {"temperature": 0.4}]},
                     "depth": "unknown-product-depth", "argv": ["--add-dir", "/private"],
                     "approval_policy": "always", "features": {"plugins": False}}
    fixture = make_native(tmp_path, configuration=configuration)
    assert read(fixture)["configuration_sha256"] == digest(configuration)
    assert native_recipe(fixture["suite"], fixture["arm"], fixture["runtime"]) == fixture["runtime"]["launch"]
    assert fixture["suite"]["arms"][fixture["arm"]]["configuration"] == configuration


def test_recipe_recomputes_instead_of_echoing_runtime_launch(tmp_path):
    fixture = make_native(tmp_path)
    expected = deepcopy(fixture["runtime"]["launch"])
    fixture["runtime"]["launch"] = {"argv": ["never-run-this"], "environment": {"HOME": "/private"}}
    assert native_recipe(fixture["suite"], fixture["arm"], fixture["runtime"]) == expected


@pytest.mark.parametrize("field", ["product_sha256", "entry_sha256", "configuration_sha256", "capabilities_sha256",
                                    "profile_sha256", "plugins_sha256", "marketplace_sha256"])
def test_stale_recomputable_runtime_bindings_fail(tmp_path, field):
    fixture = make_native(tmp_path)
    fixture["runtime"][field] = "0" * 64
    persist(fixture)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture)


@pytest.mark.parametrize("change", ["unknown-field", "bad-digest", "wrong-version", "wrong-executable",
                                     "wrong-image", "argv", "environment"])
def test_runtime_schema_identity_and_launch_drift_fail(tmp_path, change):
    fixture = make_native(tmp_path)
    runtime = fixture["runtime"]
    if change == "unknown-field":
        runtime["trusted"] = True
    elif change == "bad-digest":
        runtime["host_executable_sha256"] = "D" * 64
    elif change == "wrong-version":
        runtime["host_version"] = "0.154.0"
    elif change == "wrong-executable":
        runtime["host_executable"] = "/other/codex"
    elif change == "wrong-image":
        runtime["image_digest"] = runtime["image_digest"].replace("a" * 64, "b" * 64)
    elif change == "argv":
        runtime["launch"]["argv"].insert(-1, "--add-dir=/private")
    else:
        runtime["launch"]["environment"]["HOME"] = "/private"
    persist(fixture)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture)


@pytest.mark.parametrize("change", ["product", "cache", "catalog", "extra-plugin", "home", "config", "auth-bytes"])
def test_prepared_files_cannot_drift_or_inherit_native_state(tmp_path, change):
    fixture = make_native(tmp_path)
    native = fixture["native"]
    destinations = {"product": Path(fixture["roots"]["product"]) / SELECTED_ENTRY,
                    "cache": native / "plugins/cache/forge-evaluator/neutral-product/2.7.1" / SELECTED_ENTRY,
                    "catalog": native / "marketplace/.agents/plugins/marketplace.json",
                    "extra-plugin": native / "plugins/cache/other-product/1.0/SKILL.md",
                    "home": native / "home/.config",
                    "config": native / "codex/config.toml", "auth-bytes": native / "codex/auth.json"}
    path = destinations[change]
    path.parent.mkdir(parents=True, exist_ok=True)
    if change == "catalog":
        catalog = json.loads(path.read_text(encoding="utf-8"))
        catalog["plugins"][0]["source"]["path"] = "./other-product"
        save(path, catalog)
    else:
        path.write_text("Unexpected fixture bytes", encoding="utf-8")
    # Semantic cache/catalog checks must still fail after their raw inventory is
    # consistently rebound; this is not merely another stale-digest control.
    if change in {"cache", "extra-plugin"}:
        names = fixture["plugin_names"] + (("cache/other-product/1.0/SKILL.md",) if change == "extra-plugin" else ())
        fixture["runtime"]["plugins_sha256"] = digest(regular_entries(native / "plugins", names))
        persist(fixture)
    elif change == "catalog":
        fixture["runtime"]["marketplace_sha256"] = digest(regular_entries(native / "marketplace", fixture["marketplace_names"]))
        persist(fixture)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture)


@pytest.mark.parametrize("entry", ["/etc/passwd", "../outside.md", "docs/../docs/review's entry.md",
                                     "docs/missing.md", "docs", "docs/escape.md"])
def test_selected_entry_is_explicit_canonical_contained_regular_file(tmp_path, entry):
    fixture = make_native(tmp_path)
    if entry == "docs/escape.md":
        outside = tmp_path / "outside.md"
        outside.write_text("Not part of this product", encoding="utf-8")
        link(fixture["native"] / "plugins/cache/forge-evaluator/neutral-product/2.7.1/docs/escape.md", str(outside))
    fixture["runtime"]["selected_entry"] = entry
    persist(fixture)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture)


@pytest.mark.parametrize("change", ["raw-bytes", "wrong-root", "missing-control", "different-locator", "link",
                                     "wrong-image", "extra-command", "reordered", "nonzero", "boolean-exit",
                                     "rust-field-name", "different-install", "disabled-list", "other-source",
                                     "extra-installed", "configured-list", "unfiltered-list", "malformed-json", "duplicate-key"])
def test_preparation_is_exact_bound_private_observation_sequence(tmp_path, change):
    fixture = make_native(tmp_path)
    preparation = fixture["preparation"]
    operations = preparation["executions"]
    if change == "raw-bytes":
        fixture["preparation_path"].write_text("{}", encoding="utf-8")
    elif change == "wrong-root":
        fixture["preparation_path"].rename(fixture["suite_root"] / "native-preparation.json")
    elif change == "missing-control":
        fixture["roots"]["private"].pop()
    elif change == "different-locator":
        fixture["runtime"]["preparation"]["path"] = "../private/native-preparation.json"
        persist(fixture)
    elif change == "link":
        target = fixture["preparation_path"].with_name("other.json")
        fixture["preparation_path"].rename(target)
        link(fixture["preparation_path"], str(target))
    else:
        if change == "wrong-image":
            preparation["image_digest"] = "other@sha256:" + "b" * 64
        elif change == "extra-command":
            operations.append(deepcopy(operations[0]))
        elif change == "reordered":
            operations[1:3] = reversed(operations[1:3])
        elif change in {"nonzero", "boolean-exit"}:
            operations[2]["returncode"] = 1 if change == "nonzero" else False
        elif change == "configured-list":
            operations[-1]["argv"] = [HOST, "plugin", "list", "--marketplace", "forge-evaluator", "--json"]
        elif change == "unfiltered-list":
            operations[-1]["argv"] = [HOST, *expected_config(product=True), "plugin", "list", "--json"]
        elif change == "malformed-json":
            operations[2]["stdout"] = "[not JSON"
        elif change == "duplicate-key":
            operations[1]["stdout"] = ('{"marketplaceName":"forge-evaluator",'
                                       '"installedRoot":"/marketplace","alreadyAdded":false,"alreadyAdded":false}')
        elif change in {"rust-field-name", "different-install"}:
            record = json.loads(operations[2]["stdout"])
            if change == "rust-field-name":
                record["installed_path"] = record.pop("installedPath")
            else:
                record["installedPath"] = "/codex/plugins/cache/forge-evaluator/other/2.7.1"
            operations[2]["stdout"] = json.dumps(record)
        else:
            listing = json.loads(operations[-1]["stdout"])
            if change == "disabled-list":
                listing["installed"][0]["enabled"] = False
            elif change == "other-source":
                listing["installed"][0]["source"]["path"] = "/other/payload"
            else:
                listing["installed"].append(deepcopy(listing["installed"][0]))
            operations[-1]["stdout"] = json.dumps(listing)
        # Rebind raw bytes: these cases must reject semantics, not only a stale hash.
        persist(fixture, preparation=True)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture)


@pytest.mark.parametrize("change", ["version", "tool", "home", "codex-home", "optimize"])
def test_recipe_rejects_unsupported_native_settings_before_processes(tmp_path, change):
    fixture = make_native(tmp_path)
    host = fixture["suite"]["host"]
    if change == "version":
        host["version"] = "codex-cli 0.155.0"
    elif change == "tool":
        host["capabilities"]["tools"] = ["shell", "invented-tool"]
    else:
        key = {"home": "HOME", "codex-home": "CODEX_HOME", "optimize": "PYTHONOPTIMIZE"}[change]
        host["environment"][key] = "wrong"
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        native_recipe(fixture["suite"], fixture["arm"], fixture["runtime"])


def test_equal_fixed_environment_and_no_declared_tools_are_valid(tmp_path):
    fixture = make_native(tmp_path)
    fixture["suite"]["host"]["environment"].update(fixture["runtime"]["launch"]["environment"])
    fixture["suite"]["host"]["capabilities"]["tools"] = []
    fixture["runtime"]["capabilities_sha256"] = digest(fixture["suite"]["host"]["capabilities"])
    assert native_recipe(fixture["suite"], fixture["arm"], fixture["runtime"]) == fixture["runtime"]["launch"]


def prepare_auth(fixture, tmp_path):
    auth = tmp_path / "external-credentials/native-auth.json"
    auth.parent.mkdir()
    auth.write_text("Synthetic private fixture; no provider credentials.\n", encoding="utf-8")
    fixture["suite"]["host"]["credentials"] = "codex-native-auth"
    (fixture["native"] / "codex/auth.json").touch()
    return auth


def forbid_credential_reads(monkeypatch, auth):
    """Allow stat/fstat and read-only descriptor opens, but forbid content I/O."""
    identity = auth.stat()
    def is_credential(path_or_fd):
        try:
            info = os.fstat(path_or_fd) if isinstance(path_or_fd, int) else os.stat(path_or_fd)
        except (OSError, TypeError, ValueError):
            return False
        return (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino)
    def forbidden(*args, **kwargs):
        pytest.fail("Native runtime validation must never read or change credential bytes")
    class MetadataOnly:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def __getattr__(self, name):
            if name in {"read", "read1", "readinto", "readinto1", "readline", "readlines", "peek", "write", "writelines"}:
                return forbidden
            return getattr(self.stream, name)
        def __iter__(self):
            forbidden()
    def guard_open(original):
        def guarded(file, mode="r", *args, **kwargs):
            protected = is_credential(file)
            if protected and any(flag in mode for flag in "wax+"):
                forbidden()
            stream = original(file, mode, *args, **kwargs)
            return MetadataOnly(stream) if protected else stream
        return guarded
    for module in (builtins, io):
        monkeypatch.setattr(module, "open", guard_open(module.open))
    for name in ("read", "pread", "write", "pwrite"):
        if hasattr(os, name):
            original = getattr(os, name)
            def guarded(fd, *args, _original=original, **kwargs):
                if is_credential(fd):
                    forbidden()
                return _original(fd, *args, **kwargs)
            monkeypatch.setattr(os, name, guarded)


def test_auth_validation_uses_metadata_without_reading_hashing_or_copying_bytes(tmp_path, monkeypatch):
    fixture = make_native(tmp_path)
    auth = prepare_auth(fixture, tmp_path)
    def other_files():
        return {path.relative_to(tmp_path).as_posix(): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file() and path != auth}
    before, auth_metadata = other_files(), auth.stat()
    forbid_credential_reads(monkeypatch, auth)
    assert read(fixture, auth_file=auth) == fixture["runtime"]
    assert other_files() == before
    after = auth.stat()
    assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
        auth_metadata.st_dev, auth_metadata.st_ino, auth_metadata.st_size, auth_metadata.st_mtime_ns)
    assert (fixture["native"] / "codex/auth.json").stat().st_size == 0


def test_private_assessor_file_cannot_be_relabelled_as_native_auth(tmp_path, monkeypatch):
    fixture = make_native(tmp_path)
    prepare_auth(fixture, tmp_path)
    private_file = fixture["suite_root"] / "private-auth-fixture.json"
    private_file.write_text("Synthetic private assessor data; never a credential source.\n", encoding="utf-8")
    forbid_credential_reads(monkeypatch, private_file)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture, auth_file=private_file)


@pytest.mark.parametrize("change", ["missing-argument", "missing-path", "undeclared-file", "directory", "symlink", "parent-symlink",
                                     "placeholder-alias", "missing-placeholder", "nonempty-placeholder",
                                     "directory-placeholder", "symlink-placeholder"])
def test_auth_reference_file_type_and_empty_mountpoint_must_agree(tmp_path, monkeypatch, change):
    fixture = make_native(tmp_path)
    auth = prepare_auth(fixture, tmp_path)
    placeholder = fixture["native"] / "codex/auth.json"
    supplied = auth
    if change == "missing-argument":
        supplied = None
    elif change == "missing-path":
        supplied = auth.with_name("absent.json")
    elif change == "undeclared-file":
        fixture["suite"]["host"]["credentials"] = None
    elif change == "directory":
        supplied = auth.parent
    elif change == "symlink":
        supplied = auth.with_name("linked.json")
        link(supplied, str(auth))
    elif change == "parent-symlink":
        linked = tmp_path / "linked-credentials"
        link(linked, str(auth.parent), directory=True)
        supplied = linked / auth.name
    elif change == "placeholder-alias":
        supplied = placeholder
    elif change == "nonempty-placeholder":
        placeholder.write_text("Unexpected pre-existing auth bytes", encoding="utf-8")
    else:
        placeholder.unlink()
        if change == "directory-placeholder":
            placeholder.mkdir()
        elif change == "symlink-placeholder":
            link(placeholder, str(auth))
    forbid_credential_reads(monkeypatch, auth)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture, auth_file=supplied)


@pytest.mark.parametrize("source", ["omitted", "exact", "null"])
def test_preparation_marketplace_source_distinguishes_absent_from_explicit_null(tmp_path, source):
    fixture = make_native(tmp_path)
    operation = fixture["preparation"]["executions"][-1]
    listing = json.loads(operation["stdout"])
    selected = listing["installed"][0]
    assert "marketplaceSource" not in selected
    if source != "omitted":
        selected["marketplaceSource"] = (
            {"sourceType": "local", "source": "/marketplace"} if source == "exact" else None)
    operation["stdout"] = json.dumps(listing)
    persist(fixture, preparation=True)
    if source == "null":
        with pytest.raises(ValueError, match="^loading-unqualified:"):
            read(fixture)
    else:
        assert read(fixture) == fixture["runtime"]


@pytest.mark.parametrize("root_bytes,accepted", [
    (b'{"unfinished unrelated metadata":\n', True),
    (b'\xff', True),
    (b'{"$schema":"https://example.invalid/unrelated",'
     b'"$schema":"https://agent-plugins.org/schemas/plugin.v1.json"}', False),
], ids=["malformed-json", "invalid-utf8", "last-schema-overrides"])
def test_root_manifest_classification_preserves_native_format_precedence(tmp_path, root_bytes, accepted):
    fixture = make_native(tmp_path)
    runtime, native = fixture["runtime"], fixture["native"]
    cache = runtime["installed"]["path"].removeprefix("/codex/plugins/")
    for root in (fixture["source"], Path(fixture["roots"]["product"]),
                 native / "plugins" / cache, native / "marketplace/payload"):
        (root / "plugin.json").write_bytes(root_bytes)
    product_names = tuple(name.removeprefix(cache + "/") for name in fixture["plugin_names"]) + ("plugin.json",)
    product_hash = digest(regular_entries(fixture["source"], product_names))
    fixture["suite"]["arms"][fixture["arm"]]["product"]["inventory_sha256"] = product_hash
    runtime["product_sha256"] = runtime["installed"]["inventory_sha256"] = product_hash
    runtime["plugins_sha256"] = digest(regular_entries(
        native / "plugins", fixture["plugin_names"] + (cache + "/plugin.json",)))
    runtime["marketplace_sha256"] = digest(regular_entries(
        native / "marketplace", fixture["marketplace_names"] + ("payload/plugin.json",)))
    save(fixture["suite_root"] / "suite.json", fixture["suite"])
    persist(fixture)
    # Actual Codex metadata/evaluator records remain strict; only root-format
    # classification follows native UTF-8/JSON fallback and last-key semantics.
    if accepted:
        assert read(fixture) == runtime
    else:
        with pytest.raises(ValueError, match="^loading-unqualified:"):
            read(fixture)


def test_omitting_assessor_from_private_roots_cannot_expose_oracle_as_auth(tmp_path, monkeypatch):
    fixture = make_native(tmp_path)
    prepare_auth(fixture, tmp_path)
    fixture["roots"]["private"] = [str(fixture["native"].parent / "private")]
    oracle = fixture["suite_root"] / "checks/oracle.py"
    forbid_credential_reads(monkeypatch, oracle)
    with pytest.raises(ValueError, match="^loading-unqualified:"):
        read(fixture, auth_file=oracle)
