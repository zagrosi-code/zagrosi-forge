from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event, Thread
from typing import Any, Callable

from jsonschema import Draft202012Validator
import pytest

from zagrosi_forge.install.contracts import (
    BundleEntry,
    BundleManifest,
    ForgeError,
    RunnerProvenance,
    RunnerState,
    canonical_json_bytes,
)
from zagrosi_forge.install.metadata import load_installed_trusted_policy_set
from zagrosi_forge.install.paths import PlatformPathAuthority
from zagrosi_forge.install.policies import LIMIT_POLICY


_PROJECT_ROOT = Path(__file__).parents[2]
_FIXTURES = Path(__file__).parents[1] / "fixtures/package/bundle"
_VALID = _FIXTURES / "valid"
_DIRTY = _FIXTURES / "dirty"
_POLICIES = _FIXTURES / "policies"
_EXPECTED = _FIXTURES / "expected"
_EXPECTED_PATHS = tuple(json.loads((_EXPECTED / "base-paths.json").read_text()))
_EXPECTED_CODEXIGNORE = tuple(
    json.loads((_EXPECTED / "codexignore-lines.json").read_text())
)
_EXPECTED_SOURCE_MEMBERS = tuple(
    json.loads((_EXPECTED / "source-members.json").read_text())
)
_VALIDATION_ONLY = (".agents/plugins/marketplace.json", ".codexignore")
_EXECUTABLES = frozenset({"scripts/deep_skills.py", "scripts/zagrosi_skills.py"})
_DIRTY_CANARY = b"ZAGROSI_DIRTY_SECRET_"
_EXTERNAL_CANARY = b"ZAGROSI_EXTERNAL_SECRET_DO_NOT_COPY\n"
_PLUGIN_MANIFEST = ".codex-plugin/plugin.json"
_BUNDLE_MANIFEST = ".codex-plugin/bundle-manifest.json"


def _api() -> Any:
    import zagrosi_forge.install.bundle as bundle

    return bundle


def _runner() -> RunnerProvenance:
    return RunnerProvenance(
        state=RunnerState.VERIFIED_INSTALLED_DISTRIBUTION,
        origin="installed-wheel",
        artifact_digest="a" * 64,
        runner_version="0.2.0",
        verification_authority="wheel-sha256",
        policy_digest="b" * 64,
    )


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _required_fixture_paths() -> frozenset[str]:
    trusted = load_installed_trusted_policy_set()
    return frozenset(
        (
            *_EXPECTED_PATHS,
            *_VALIDATION_ONLY,
            *trusted.authority_file_digests,
            *trusted.required_regular_references,
        )
    )


def _expected_source_members() -> dict[str, dict[str, object]]:
    selected = {record["path"]: record for record in _EXPECTED_SOURCE_MEMBERS}
    assert len(selected) == len(_EXPECTED_SOURCE_MEMBERS)
    return selected


def _materialize_candidate(destination: Path, *, dirty: bool = False) -> Path:
    shutil.copytree(_VALID, destination)
    required = _required_fixture_paths()
    expected = _expected_source_members()
    assert set(expected) == set(required)
    for relative in sorted(required, key=str.encode):
        source = _PROJECT_ROOT / relative
        target = destination / relative
        assert source.is_file(), f"bundle fixture source is missing: {relative}"
        raw = source.read_bytes()
        record = expected[relative]
        assert record["size"] == len(raw), f"bundle fixture size drift: {relative}"
        assert record["sha256"] == hashlib.sha256(raw).hexdigest(), (
            f"bundle fixture digest drift: {relative}"
        )
        _copy_file(source, target)
    if dirty:
        dirty_tree = json.loads((_DIRTY / "tree.json").read_bytes())
        for relative, content in dirty_tree.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return destination


def _validated_package(root: Path) -> Any:
    from zagrosi_forge.install.metadata import validate_package

    result = validate_package(
        root,
        runner=_runner(),
        trusted=load_installed_trusted_policy_set(),
        path_authority=PlatformPathAuthority(),
    )
    assert result.is_ok, result.error
    return result.unwrap()


def _base_bundle(root: Path, *, dirty: bool = False) -> Any:
    api = _api()
    candidate = _materialize_candidate(root, dirty=dirty)
    package = _validated_package(candidate)
    policy = api.load_trusted_bundle_policy()
    authority = PlatformPathAuthority()
    try:
        with authority.open_source_root(candidate) as source:
            with api.open_bundle_snapshot(source, policy) as snapshot:
                return api.enumerate_base_bundle(package, snapshot, policy)
    finally:
        package.source_snapshot.close()


def _entry_paths(bundle: Any) -> tuple[str, ...]:
    entries = getattr(bundle, "entries", bundle.manifest.entries)
    return tuple(entry.path for entry in entries)


def _entry_bytes(bundle: Any) -> dict[str, bytes]:
    return dict(bundle.entry_bytes)


def _manifest_domain(manifest: BundleManifest) -> dict[str, object]:
    return {
        "aggregate_size": manifest.aggregate_size,
        "base_version": manifest.base_version,
        "entries": [asdict(entry) for entry in manifest.entries],
        "normalization_profile": manifest.normalization_profile,
        "policy_digest": manifest.policy_digest,
        "schema_version": manifest.schema_version,
    }


def _rendered_domain(rendered: Any) -> dict[str, object]:
    return {
        "base_payload_digest": rendered.base.manifest.payload_digest,
        "entries": [asdict(entry) for entry in rendered.entries],
        "install_version": rendered.install_version,
        "policy_digest": rendered.base.manifest.policy_digest,
        "transformation_profile": rendered.transformation_profile,
    }


def _bundle_canonical_json(value: object) -> bytes:
    return _api().canonical_bundle_json_bytes(value)


def _bundle_error(call: Callable[[], object], code: str) -> ForgeError:
    with pytest.raises(ForgeError) as caught:
        call()
    error = caught.value
    assert error.code == code
    assert error.exit_category == 12
    assert "ZAGROSI_" not in str(error)
    return error


def _identity(path: Path) -> tuple[int, int]:
    status = path.stat(follow_symlinks=False)
    return status.st_dev, status.st_ino


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    link.symlink_to(target, target_is_directory=True)


def _private_directory(path: Path) -> Path:
    if os.name != "nt":
        path.mkdir(mode=0o700)
        return path

    import zagrosi_forge.install.paths as paths

    parent = paths._windows_open_path(os.fspath(path.parent))
    child = 0
    try:
        child = paths._windows_create_private_directory(parent, path.name)
        assert paths._windows_private_directory(child, exact=True)
    finally:
        if child:
            paths._windows_close(child)
        paths._windows_close(parent)
    return path


def _stage_projection(
    rendered: Any, tmp_path: Path
) -> tuple[Any, Path, tuple[Any, ...]]:
    from zagrosi_forge.install.ownership import (
        create_transaction_path,
        prove_transaction_owned,
    )
    from zagrosi_forge.install.paths import validate_reference

    tmp_path.mkdir(parents=True, exist_ok=True)
    home = _private_directory(tmp_path / "codex-home")
    authority = PlatformPathAuthority()
    owned = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    plugin_root = home / "plugins"
    (plugin_root / "stages").mkdir(mode=0o700)
    relative = validate_reference(
        "stages/bundle", role="bundle-stage", limits=LIMIT_POLICY
    ).unwrap()
    claim = create_transaction_path(
        owned, relative, transaction_id="bundle-stage"
    ).unwrap()
    path = authority.prove_descendant(owned, relative, expected_depth=2).unwrap()
    proof = prove_transaction_owned(path, claim=claim).unwrap()
    staged = _api().stage_marketplace(rendered, proof)
    return staged, plugin_root / "stages/bundle", (proof, path, owned)


def _close_all(capabilities: tuple[Any, ...]) -> None:
    for capability in capabilities:
        capability.close()


def _rewrite_record_digest(document: dict[str, object]) -> dict[str, object]:
    updated = copy.deepcopy(document)
    updated.pop("record_digest", None)
    updated["record_digest"] = hashlib.sha256(canonical_json_bytes(updated)).hexdigest()
    return updated


def _apply_policy_case(
    document: dict[str, object], case: dict[str, object]
) -> dict[str, object]:
    changed = copy.deepcopy(document)
    operation = case["operation"]
    if operation == "identity":
        return changed
    path = tuple(case["path"])
    parent: Any = changed
    for component in path[:-1]:
        parent = parent[component]
    leaf = path[-1]
    if operation == "remove":
        del parent[leaf]
    elif operation == "set":
        parent[leaf] = case["value"]
    elif operation == "append":
        parent[leaf].append(case["value"])
    elif operation == "append-sorted":
        parent[leaf].append(case["value"])
        parent[leaf].sort(key=lambda item: item.encode("utf-8"))
    elif operation == "append-many-sorted":
        parent[leaf].extend(case["values"])
        parent[leaf].sort(key=lambda item: item.encode("utf-8"))
    elif operation == "duplicate-first":
        parent[leaf].append(parent[leaf][0])
    else:  # pragma: no cover - fixture vocabulary is deliberately closed
        raise AssertionError(operation)
    return _rewrite_record_digest(changed)


def _write_policy(root: Path, document: dict[str, object]) -> None:
    target = root / "src/zagrosi_forge/install/bundle-policy.json"
    target.write_bytes(canonical_json_bytes(document, final_newline=True))


def test_clean_and_canary_dirty_trees_have_same_base_manifest(
    tmp_path: Path,
) -> None:
    clean = _base_bundle(tmp_path / "clean")
    dirty = _base_bundle(tmp_path / "dirty", dirty=True)

    assert clean.manifest == dirty.manifest
    assert clean.manifest_bytes == dirty.manifest_bytes
    assert _entry_bytes(clean) == _entry_bytes(dirty)


def test_positive_policy_includes_exact_required_runtime_members() -> None:
    policy = _api().load_trusted_bundle_policy()

    assert tuple(policy.required_files) == _EXPECTED_PATHS
    assert tuple(policy.validation_only_files) == _VALIDATION_ONLY
    assert tuple(policy.codexignore_lines) == _EXPECTED_CODEXIGNORE
    assert frozenset(policy.executable_files) == _EXECUTABLES
    assert all("*" not in path and "?" not in path for path in policy.required_files)
    for skill in ("zagrosi-project", "zagrosi-plan", "zagrosi-implement"):
        assert f"skills/{skill}/SKILL.md" in policy.required_files
        assert f"skills/{skill}/agents/openai.yaml" in policy.required_files
    assert "src/zagrosi_forge/_vendor/vendor-receipt.json" in policy.required_files
    assert "src/zagrosi_forge/_vendor/tomlkit-LICENSE" in policy.required_files
    assert "src/zagrosi_forge/install/bundle-policy.json" in policy.required_files
    runtime_modules = {
        path.relative_to(_PROJECT_ROOT).as_posix()
        for path in (_PROJECT_ROOT / "src/zagrosi_forge/install").rglob("*.py")
    }
    assert runtime_modules <= set(policy.required_files)


def test_valid_fixture_projection_is_complete_and_content_addressed() -> None:
    required = _required_fixture_paths()
    expected = _expected_source_members()
    seed_paths = {
        path.relative_to(_VALID).as_posix()
        for path in _VALID.rglob("*")
        if path.is_file()
    }

    assert set(expected) == set(required)
    assert seed_paths <= set(required)
    for relative, record in expected.items():
        raw = (_PROJECT_ROOT / relative).read_bytes()
        assert record == {
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }


@pytest.mark.parametrize(
    "mutation",
    (
        "undeclared-reference",
        "dot-reference",
        "repeated-dot-reference",
        "double-separator-reference",
        "absolute-reference",
        "root-backslash-reference",
        "case-reference",
        "windows-dot-reference",
        "parent-reference",
        "agent-rebind",
        "unreachable-reference",
    ),
)
def test_skill_agent_reference_graph_must_be_closed(
    tmp_path: Path, mutation: str
) -> None:
    api = _api()
    root = _materialize_candidate(tmp_path / mutation)
    skill = root / "skills/zagrosi-project/SKILL.md"
    agent = root / "skills/zagrosi-project/agents/openai.yaml"
    reference_mutations = {
        "undeclared-reference": "references/not-declared.md",
        "dot-reference": "./references/not-declared.md",
        "repeated-dot-reference": "././references/not-declared.md",
        "double-separator-reference": ".//references/not-declared.md",
        "absolute-reference": "/references/not-declared.md",
        "root-backslash-reference": "\\references\\not-declared.md",
        "case-reference": "References/not-declared.md",
        "windows-dot-reference": ".\\references\\not-declared.md",
        "parent-reference": "../references/not-declared.md",
    }
    if mutation in reference_mutations:
        skill.write_text(
            skill.read_text(encoding="utf-8")
            + f"\nRead `{reference_mutations[mutation]}`.\n",
            encoding="utf-8",
        )
    elif mutation == "agent-rebind":
        agent.write_text(
            agent.read_text(encoding="utf-8").replace(
                "$zagrosi-forge:zagrosi-project",
                "$zagrosi-forge:zagrosi-plan",
            ),
            encoding="utf-8",
        )
    else:
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "`references/interview.md`", "the bundled interview guide"
            ),
            encoding="utf-8",
        )
    package = _validated_package(root)
    policy = api.load_trusted_bundle_policy()
    authority = PlatformPathAuthority()
    try:
        with authority.open_source_root(root) as source:
            with api.open_bundle_snapshot(source, policy) as snapshot:
                _bundle_error(
                    lambda: api.enumerate_base_bundle(package, snapshot, policy),
                    "bundle.unexpected_member",
                )
    finally:
        package.source_snapshot.close()


@pytest.mark.parametrize(
    "relative",
    (
        "src/zagrosi_forge/_vendor/tomlkit/api.py",
        "src/zagrosi_forge/_vendor/tomlkit-LICENSE",
    ),
)
def test_vendor_receipt_binds_selected_runtime_files_and_license(
    tmp_path: Path, relative: str
) -> None:
    api = _api()
    root = _materialize_candidate(tmp_path / "vendor-tamper")
    target = root / relative
    target.write_bytes(target.read_bytes() + b"\n# candidate tamper\n")
    package = _validated_package(root)
    policy = api.load_trusted_bundle_policy()
    authority = PlatformPathAuthority()
    try:
        with authority.open_source_root(root) as source:
            with api.open_bundle_snapshot(source, policy) as snapshot:
                _bundle_error(
                    lambda: api.enumerate_base_bundle(package, snapshot, policy),
                    "bundle.digest_mismatch",
                )
    finally:
        package.source_snapshot.close()


@pytest.mark.parametrize("field", ("display_name", "category"))
def test_marketplace_projection_must_match_trusted_generated_contract(
    tmp_path: Path, field: str
) -> None:
    api = _api()
    root = _materialize_candidate(tmp_path / field)
    marketplace_path = root / ".agents/plugins/marketplace.json"
    marketplace = json.loads(marketplace_path.read_bytes())
    if field == "display_name":
        marketplace["interface"]["displayName"] = "Candidate Variant"
    else:
        marketplace["plugins"][0]["category"] = "Candidate Variant"
    marketplace_path.write_bytes(canonical_json_bytes(marketplace, final_newline=True))
    package = _validated_package(root)
    policy = api.load_trusted_bundle_policy()
    authority = PlatformPathAuthority()
    try:
        with authority.open_source_root(root) as source:
            with api.open_bundle_snapshot(source, policy) as snapshot:
                _bundle_error(
                    lambda: api.enumerate_base_bundle(package, snapshot, policy),
                    "bundle.policy_invalid",
                )
    finally:
        package.source_snapshot.close()


def test_bundle_excludes_vcs_venv_cache_tests_planning_and_secrets(
    tmp_path: Path,
) -> None:
    base = _base_bundle(tmp_path / "dirty", dirty=True)
    paths = _entry_paths(base)
    rendered = b"".join(_entry_bytes(base).values()) + base.manifest_bytes

    assert paths == _EXPECTED_PATHS
    assert _DIRTY_CANARY not in rendered
    forbidden = (
        ".git/",
        ".venv/",
        ".pytest_cache/",
        ".ruff_cache/",
        "tests/",
        "examples/",
        "planning/",
        "reports/",
        "dist/",
        "build/",
        "secrets/",
    )
    assert not any(path.startswith(forbidden) for path in paths)
    assert "uv.lock" not in paths
    assert "unknown-secret.txt" not in paths
    assert not any("__pycache__" in path or path.endswith(".pyc") for path in paths)


def test_bundle_rejects_symlink_hardlink_special_and_unknown_member(
    tmp_path: Path,
) -> None:
    api = _api()
    policy = api.load_trusted_bundle_policy()
    external_file = tmp_path / "external.bin"
    external_file.write_bytes(_EXTERNAL_CANARY)
    external_identity = _identity(external_file)
    external_directory = tmp_path / "external-directory"
    external_directory.mkdir()
    (external_directory / "canary.bin").write_bytes(_EXTERNAL_CANARY)

    for kind in ("symlink", "hardlink", "special"):
        root = _materialize_candidate(tmp_path / kind)
        package = _validated_package(root)
        target = root / "README.md"
        target.unlink()
        if kind == "symlink":
            _directory_link(target, external_directory)
        elif kind == "hardlink":
            os.link(external_file, target)
        else:
            target.mkdir()
        authority = PlatformPathAuthority()
        try:
            with authority.open_source_root(root) as source:
                _bundle_error(
                    lambda: api.open_bundle_snapshot(source, policy),
                    "bundle.unsafe_file_type",
                )
        finally:
            package.source_snapshot.close()

    _bundle_error(
        lambda: api.validate_bundle_member_paths(
            (*policy.required_files, "unknown-secret.txt"), policy
        ),
        "bundle.unexpected_member",
    )
    assert external_file.read_bytes() == _EXTERNAL_CANARY
    assert _identity(external_file) == external_identity


def test_bundle_rejects_case_unicode_and_duplicate_path_collision() -> None:
    api = _api()
    policy = api.load_trusted_bundle_policy()
    cases = (
        ("README.md", "readme.md"),
        ("skills/caf\u00e9/SKILL.md", "skills/cafe\u0301/SKILL.md"),
        ("README.md", "README.md"),
    )
    for paths in cases:
        _bundle_error(
            lambda paths=paths: api.validate_bundle_member_paths(paths, policy),
            "bundle.normalization_collision",
        )


def test_handle_read_change_fails_without_external_byte_leak(
    tmp_path: Path,
) -> None:
    api = _api()
    root = _materialize_candidate(tmp_path / "candidate")
    package = _validated_package(root)
    policy = api.load_trusted_bundle_policy()
    external = tmp_path / "outside-canary"
    external.write_bytes(_EXTERNAL_CANARY)
    external_identity = _identity(external)
    target = root / "README.md"
    original_bytes = target.read_bytes()
    held = root / ".README.md.original"
    start = Event()
    finished = Event()
    state = {"moved": False, "linked": False}
    actor_errors: list[BaseException] = []

    def substitute_after_snapshot() -> None:
        start.wait()
        try:
            try:
                target.rename(held)
            except OSError:
                return
            state["moved"] = True
            try:
                os.link(external, target)
            except OSError:
                held.rename(target)
                state["moved"] = False
                return
            state["linked"] = True
        except BaseException as exc:  # pragma: no cover - platform diagnostics
            actor_errors.append(exc)
        finally:
            finished.set()

    actor = Thread(target=substitute_after_snapshot, daemon=True)
    actor.start()
    authority = PlatformPathAuthority()
    observed_bundle: Any | None = None
    observed_error: ForgeError | None = None
    try:
        with authority.open_source_root(root) as source:
            with api.open_bundle_snapshot(source, policy) as snapshot:
                start.set()
                assert finished.wait(10), "source substitution actor timed out"
                try:
                    observed_bundle = api.enumerate_base_bundle(
                        package, snapshot, policy
                    )
                except ForgeError as exc:
                    assert exc.code == "bundle.source_changed"
                    assert exc.exit_category == 12
                    observed_error = exc
    finally:
        package.source_snapshot.close()
        actor.join(timeout=10)

    assert not actor.is_alive()
    assert not actor_errors
    if state["linked"]:
        target.unlink()
    if state["moved"]:
        held.rename(target)

    assert external.read_bytes() == _EXTERNAL_CANARY
    assert _identity(external) == external_identity
    assert target.read_bytes() == original_bytes
    assert observed_bundle is not None or observed_error is not None
    if observed_bundle is not None:
        rendered = b"".join(observed_bundle.entry_bytes.values())
        rendered += observed_bundle.manifest_bytes
        assert _EXTERNAL_CANARY not in rendered
        assert observed_bundle.entry_bytes["README.md"] == original_bytes
    if observed_error is not None:
        assert _EXTERNAL_CANARY.decode().strip() not in observed_error.safe_message


def test_base_digest_derives_fixed_install_version(tmp_path: Path) -> None:
    from zagrosi_forge.install.version import derive_install_version

    base = _base_bundle(tmp_path / "candidate")
    rendered = _api().derive_install_projection(base)
    expected = derive_install_version(
        base.manifest.base_version, base.manifest.payload_digest
    )

    assert rendered.install_version == expected
    assert rendered.install_version.endswith(base.manifest.payload_digest[:32])
    assert len(rendered.install_version.rsplit("-", 1)[1]) == 32
    assert _api().derive_install_projection(base).install_version == expected


def test_rendered_digest_commits_to_only_declared_transformations(
    tmp_path: Path,
) -> None:
    base = _base_bundle(tmp_path / "candidate")
    rendered = _api().derive_install_projection(base)
    base_bytes = _entry_bytes(base)
    rendered_bytes = _entry_bytes(rendered)
    changed = {path for path in base_bytes if base_bytes[path] != rendered_bytes[path]}

    assert changed == {_PLUGIN_MANIFEST}
    assert json.loads(base_bytes[_PLUGIN_MANIFEST])["version"] == (
        base.manifest.base_version
    )
    assert json.loads(rendered_bytes[_PLUGIN_MANIFEST])["version"] == (
        rendered.install_version
    )
    assert (
        rendered.rendered_payload_digest
        == hashlib.sha256(
            _bundle_canonical_json(_rendered_domain(rendered))
        ).hexdigest()
    )
    assert rendered.rendered_payload_digest != base.manifest.payload_digest


def test_staged_marketplace_points_to_plugins_zagrosi_forge(
    tmp_path: Path,
) -> None:
    rendered = _api().derive_install_projection(_base_bundle(tmp_path / "candidate"))
    staged, root, capabilities = _stage_projection(rendered, tmp_path / "stage")
    try:
        document = json.loads((root / ".agents/plugins/marketplace.json").read_bytes())
        assert document["plugins"] == [
            {
                "category": "Coding",
                "name": "zagrosi-forge",
                "policy": {
                    "authentication": "ON_INSTALL",
                    "installation": "AVAILABLE",
                },
                "source": {
                    "path": "./plugins/zagrosi-forge",
                    "source": "local",
                },
            }
        ]
        assert staged.marketplace_relative.value == ".agents/plugins/marketplace.json"
        assert staged.plugin_root_relative.value == "plugins/zagrosi-forge"
        assert (root / "plugins/zagrosi-forge/.codex-plugin/plugin.json").is_file()
    finally:
        _close_all(capabilities)


def test_dot_codexignore_drift_fails_but_never_changes_enumeration(
    tmp_path: Path,
) -> None:
    api = _api()
    clean = _base_bundle(tmp_path / "clean", dirty=True)
    root = _materialize_candidate(tmp_path / "drift", dirty=True)
    package = _validated_package(root)
    policy = api.load_trusted_bundle_policy()
    (root / ".codexignore").write_text("**/*\n", encoding="utf-8")
    authority = PlatformPathAuthority()
    try:
        with authority.open_source_root(root) as source:
            _bundle_error(
                lambda: api.open_bundle_snapshot(source, policy),
                "bundle.policy_invalid",
            )
    finally:
        package.source_snapshot.close()

    assert _entry_paths(clean) == _EXPECTED_PATHS
    assert tuple(policy.required_files) == _EXPECTED_PATHS
    assert "unknown-secret.txt" not in policy.required_files


def test_native_cache_manifest_equals_rendered_plugin_subtree(
    tmp_path: Path,
) -> None:
    api = _api()
    rendered = api.derive_install_projection(_base_bundle(tmp_path / "candidate"))
    staged, root, capabilities = _stage_projection(rendered, tmp_path / "stage")
    try:
        evidence = api.verify_staged_marketplace(staged, rendered)
        plugin_root = root / "plugins/zagrosi-forge"
        actual = {
            path.relative_to(plugin_root).as_posix(): path.read_bytes()
            for path in plugin_root.rglob("*")
            if path.is_file()
        }
        expected = _entry_bytes(rendered) | {
            _BUNDLE_MANIFEST: staged.bundle_manifest_bytes
        }
        assert actual == expected
        assert staged.plugin_entries == rendered.entries
        assert evidence.payload_digest == rendered.rendered_payload_digest
    finally:
        _close_all(capabilities)


@pytest.mark.parametrize(
    "mutation",
    ("tamper", "extra-file", "extra-directory", "missing"),
)
def test_staged_verification_rejects_post_stage_inventory_drift(
    tmp_path: Path, mutation: str
) -> None:
    api = _api()
    rendered = api.derive_install_projection(_base_bundle(tmp_path / "candidate"))
    staged, root, capabilities = _stage_projection(
        rendered, tmp_path / f"stage-{mutation}"
    )
    plugin_root = root / "plugins/zagrosi-forge"
    try:
        if mutation == "tamper":
            (plugin_root / "README.md").write_bytes(b"post-stage-tamper\n")
        elif mutation == "extra-file":
            (plugin_root / "unexpected.txt").write_bytes(b"unexpected\n")
        elif mutation == "extra-directory":
            (plugin_root / "unexpected").mkdir()
        else:
            (plugin_root / "README.md").unlink()

        _bundle_error(
            lambda: api.verify_staged_marketplace(staged, rendered),
            "bundle.digest_mismatch",
        )
    finally:
        _close_all(capabilities)


def test_staged_verification_requires_live_stage_authority(tmp_path: Path) -> None:
    api = _api()
    rendered = api.derive_install_projection(_base_bundle(tmp_path / "candidate"))
    staged, _root, capabilities = _stage_projection(rendered, tmp_path / "stage")
    capabilities[1].close()
    try:
        _bundle_error(
            lambda: api.verify_staged_marketplace(staged, rendered),
            "bundle.digest_mismatch",
        )
    finally:
        _close_all(capabilities)


def test_bundle_policy_schema_and_runtime_decoder_have_parity() -> None:
    api = _api()
    schema = json.loads(
        (
            _PROJECT_ROOT
            / "src/zagrosi_forge/install/schemas/bundle-policy-v1.schema.json"
        ).read_bytes()
    )
    installed = json.loads(
        (_PROJECT_ROOT / "src/zagrosi_forge/install/bundle-policy.json").read_bytes()
    )
    corpus = json.loads((_POLICIES / "parity-corpus.json").read_bytes())
    validator = Draft202012Validator(schema)

    for case in corpus:
        document = _apply_policy_case(installed, case)
        schema_valid = validator.is_valid(document)
        runtime_valid = api.validate_bundle_policy_document(document).is_valid
        expected_schema = case.get("schema_valid", case["valid"])
        expected_runtime = case.get("runtime_valid", case["valid"])
        assert schema_valid == expected_schema, case["name"]
        assert runtime_valid == expected_runtime, case["name"]
        assert not runtime_valid or schema_valid, case["name"]


def test_bundle_manifest_schema_matches_portable_runtime_paths(tmp_path: Path) -> None:
    schema = json.loads(
        (
            _PROJECT_ROOT
            / "src/zagrosi_forge/install/schemas/bundle-manifest-v1.schema.json"
        ).read_bytes()
    )
    validator = Draft202012Validator(schema)
    base = _base_bundle(tmp_path / "candidate")
    document = json.loads(base.manifest_bytes)
    assert validator.is_valid(document)

    for unsafe in (
        "CON",
        "assets/unsafe.",
        "assets/unsafe ",
        "assets/~unsafe",
        "assets/unsafe:stream",
        "assets/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "assets/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p",
    ):
        changed = copy.deepcopy(document)
        changed["entries"][0]["path"] = unsafe
        assert not validator.is_valid(changed), unsafe
        with pytest.raises(ValueError):
            BundleEntry(**changed["entries"][0])

    collided = copy.deepcopy(document)
    duplicate = copy.deepcopy(
        next(entry for entry in collided["entries"] if entry["path"] == "README.md")
    )
    duplicate["path"] = "readme.md"
    collided["entries"].append(duplicate)
    collided["entries"].sort(key=lambda entry: entry["path"].encode("utf-8"))
    collided["aggregate_size"] += duplicate["size"]
    assert validator.is_valid(collided)
    entries = tuple(BundleEntry(**entry) for entry in collided["entries"])
    with pytest.raises(ValueError):
        BundleManifest(
            schema_version=collided["schema_version"],
            base_version=collided["base_version"],
            policy_digest=collided["policy_digest"],
            entries=entries,
            aggregate_size=collided["aggregate_size"],
            payload_digest=collided["payload_digest"],
            builder_version=collided["builder_version"],
            normalization_profile=collided["normalization_profile"],
        )


def test_candidate_bundle_policy_cannot_expand_trusted_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _api()
    root = _materialize_candidate(tmp_path / "candidate")
    package = _validated_package(root)
    trusted = api.load_trusted_bundle_policy()
    candidate = json.loads(
        (root / "src/zagrosi_forge/install/bundle-policy.json").read_bytes()
    )
    expansion = json.loads((_POLICIES / "candidate-expansion.json").read_bytes())
    candidate["required_files"].append(expansion["append_required_file"])
    _write_policy(root, _rewrite_record_digest(candidate))
    malicious = root / expansion["append_required_file"]
    malicious.parent.mkdir(parents=True)
    malicious.write_bytes(_EXTERNAL_CANARY)

    opened: list[str] = []
    authority = PlatformPathAuthority()
    try:
        with authority.open_source_root(root) as source:
            source_type = type(source)
            original_open = source_type.open_regular_file

            def observe_open(source_root: Any, reference: Any) -> Any:
                opened.append(reference.value)
                return original_open(source_root, reference)

            monkeypatch.setattr(source_type, "open_regular_file", observe_open)
            with api.open_bundle_snapshot(source, trusted) as snapshot:
                _bundle_error(
                    lambda: api.enumerate_base_bundle(package, snapshot, trusted),
                    "bundle.policy_invalid",
                )
    finally:
        package.source_snapshot.close()

    assert expansion["append_required_file"] not in opened
    assert _EXTERNAL_CANARY not in b"".join(
        (root / path).read_bytes() for path in opened if (root / path).is_file()
    )


def test_bundle_manifest_serialization_is_canonical(tmp_path: Path) -> None:
    base = _base_bundle(tmp_path / "candidate")
    manifest = base.manifest

    assert isinstance(manifest, BundleManifest)
    assert all(isinstance(entry, BundleEntry) for entry in manifest.entries)
    assert base.manifest_bytes == canonical_json_bytes(manifest, final_newline=True)
    decoded = json.loads(base.manifest_bytes)
    assert decoded["entries"] == [asdict(entry) for entry in manifest.entries]
    assert decoded["payload_digest"] == manifest.payload_digest
    assert (
        manifest.payload_digest
        == hashlib.sha256(
            _bundle_canonical_json(_manifest_domain(manifest))
        ).hexdigest()
    )
    assert tuple(entry.path for entry in manifest.entries) == _EXPECTED_PATHS


def test_manifest_is_adjacent_and_not_in_its_own_digest_domain(
    tmp_path: Path,
) -> None:
    rendered = _api().derive_install_projection(_base_bundle(tmp_path / "candidate"))
    staged, root, capabilities = _stage_projection(rendered, tmp_path / "stage")
    try:
        manifest_path = root / "plugins/zagrosi-forge" / _BUNDLE_MANIFEST
        document = json.loads(manifest_path.read_bytes())
        assert manifest_path.is_file()
        assert _BUNDLE_MANIFEST not in {entry.path for entry in rendered.entries}
        assert _BUNDLE_MANIFEST not in {entry["path"] for entry in document["entries"]}
        assert document["payload_digest"] == rendered.rendered_payload_digest
    finally:
        _close_all(capabilities)


def test_at_limit_bundle_passes_and_limit_plus_one_fails_without_stage(
    tmp_path: Path,
) -> None:
    api = _api()
    policy = api.load_trusted_bundle_policy()
    member_limit = LIMIT_POLICY.value("bundle_member_bytes")
    total_limit = LIMIT_POLICY.value("bundle_total_bytes")
    at_member_limit = BundleEntry(
        path="README.md",
        file_type="regular",
        mode=0o644,
        size=member_limit,
        sha256="a" * 64,
    )
    api.enforce_bundle_limits((at_member_limit,), policy)
    at_total_limit = tuple(
        BundleEntry(
            path=f"assets/limit-{index}.bin",
            file_type="regular",
            mode=0o644,
            size=member_limit,
            sha256=f"{index:x}" * 64,
        )
        for index in range(total_limit // member_limit)
    )
    api.enforce_bundle_limits(at_total_limit, policy)
    plus_one = BundleEntry(
        path="README.md",
        file_type="regular",
        mode=0o644,
        size=member_limit + 1,
        sha256="f" * 64,
    )
    _bundle_error(
        lambda: api.enforce_bundle_limits((plus_one,), policy),
        "bundle.limit_exceeded",
    )
    assert not (tmp_path / "stage").exists()


def test_bundle_mode_normalization_is_platform_independent() -> None:
    api = _api()
    policy = api.load_trusted_bundle_policy()
    raw_modes = (0, 0o600, 0o644, 0o700, 0o755, 0o100777)

    assert {
        api.normalize_bundle_mode("README.md", mode, policy) for mode in raw_modes
    } == {0o644}
    assert {
        api.normalize_bundle_mode("scripts/zagrosi_skills.py", mode, policy)
        for mode in raw_modes
    } == {0o755}


def test_low_level_mutation_cannot_preserve_bundle_authority(tmp_path: Path) -> None:
    api = _api()

    policy = api.load_trusted_bundle_policy()
    assert api._is_policy(policy)
    object.__setattr__(policy, "policy_digest", "f" * 64)
    assert not api._is_policy(policy)
    _bundle_error(
        lambda: api.validate_bundle_member_paths(("README.md",), policy),
        "bundle.policy_invalid",
    )

    base = _base_bundle(tmp_path / "base-candidate")
    assert api._is_canonical(base)
    object.__setattr__(base.manifest, "payload_digest", "e" * 64)
    assert not api._is_canonical(base)
    _bundle_error(
        lambda: api.derive_install_projection(base),
        "bundle.render_transform_invalid",
    )

    rendered = api.derive_install_projection(
        _base_bundle(tmp_path / "rendered-candidate")
    )
    assert api._is_rendered(rendered)
    object.__setattr__(rendered, "rendered_payload_digest", "d" * 64)
    assert not api._is_rendered(rendered)

    trusted_rendered = api.derive_install_projection(
        _base_bundle(tmp_path / "staged-candidate")
    )
    staged, _root, capabilities = _stage_projection(
        trusted_rendered, tmp_path / "stage"
    )
    try:
        assert api._is_staged(staged)
        object.__setattr__(staged, "install_version", "0.2.0+codex.local-forged")
        assert not api._is_staged(staged)
        _bundle_error(
            lambda: api.verify_staged_marketplace(staged, trusted_rendered),
            "bundle.digest_mismatch",
        )
    finally:
        _close_all(capabilities)
