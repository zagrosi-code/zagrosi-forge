from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from jsonschema import Draft202012Validator
import pytest

from zagrosi_forge.install.contracts import (
    DiagnosticReport,
    ForgeError,
    RunnerProvenance,
    RunnerState,
)
from zagrosi_forge.install.policies import LIMIT_POLICY
from zagrosi_forge.install.metadata import load_installed_trusted_policy_set
from zagrosi_forge.install.paths import PlatformPathAuthority


_PROJECT_ROOT = Path(__file__).parents[2]
_FIXTURE = Path(__file__).parents[1] / "fixtures/package/metadata/valid"
_SCHEMAS = _PROJECT_ROOT / "src/zagrosi_forge/install/schemas"
_MANIFEST = ".codex-plugin/plugin.json"
_MARKETPLACE = ".agents/plugins/marketplace.json"
_PROJECT = "pyproject.toml"
_ASSET_AT_NORMALIZED_BYTE_LIMIT = "./" + "/".join(
    ("a" * 59, "b" * 59, "c" * 59, "d" * 60)
)
_INSTALLED_TRUST = load_installed_trusted_policy_set()
_AUTHORITY_FILES = tuple(_INSTALLED_TRUST.authority_file_digests)
_REQUIRED_REGULAR = _INSTALLED_TRUST.required_regular_references


def _relative_text(reference: object) -> str:
    for name in ("value", "normalized", "relative"):
        value = getattr(reference, name, None)
        if isinstance(value, str):
            return value
    parts = getattr(reference, "parts", None)
    if isinstance(parts, tuple) and all(isinstance(part, str) for part in parts):
        return "/".join(parts)
    return str(reference)


class _StructuralPathAuthorityFake:
    def __init__(self) -> None:
        self.opened_roots: list[Path] = []
        self.opened: list[str] = []

    def open_source_root(self, raw_root: os.PathLike[str]) -> None:
        root = Path(raw_root)
        self.opened_roots.append(root)
        raise AssertionError("negative-only fake must never open candidate paths")


def _copy(source: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_FIXTURE / source, destination)


def _package_factory(tmp_path: Path, name: str = "candidate") -> Path:
    root = tmp_path / name
    _copy("plugin.json", root / _MANIFEST)
    _copy("marketplace.json", root / _MARKETPLACE)
    _copy("pyproject.toml", root / _PROJECT)
    for relative in sorted({*_AUTHORITY_FILES, *_REQUIRED_REGULAR}):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_PROJECT_ROOT / relative, destination)
    return root


def _trusted():
    from zagrosi_forge.install.metadata import load_installed_trusted_policy_set

    return load_installed_trusted_policy_set()


def test_trusted_policy_set_constructor_is_private(tmp_path: Path) -> None:
    from zagrosi_forge.install.metadata import TrustedPolicySet

    root = _package_factory(tmp_path)
    digests = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in _AUTHORITY_FILES
    }

    with pytest.raises(TypeError):
        TrustedPolicySet(
            limits=LIMIT_POLICY,
            authority_version="1.0",
            authority_file_digests=digests,
            required_regular_references=_REQUIRED_REGULAR,
        )


def test_installed_authority_ignores_candidate_bytes(tmp_path: Path) -> None:
    from zagrosi_forge.install.metadata import load_installed_trusted_policy_set

    root = _package_factory(tmp_path)
    before = load_installed_trusted_policy_set()
    (root / "src/zagrosi_forge/install/limit-policy.json").write_text(
        '{"policy_version":"2.0","references":["evil.py"]}\n',
        encoding="utf-8",
    )
    after = load_installed_trusted_policy_set()

    assert before == after
    assert set(after.authority_file_digests) == set(_AUTHORITY_FILES)
    assert after.required_regular_references == tuple(sorted(_REQUIRED_REGULAR))


def _runner(*, verified: bool = True) -> RunnerProvenance:
    return RunnerProvenance(
        state=(
            RunnerState.VERIFIED_INSTALLED_DISTRIBUTION
            if verified
            else RunnerState.UNVERIFIED_SELF_ROOT
        ),
        origin="installed-wheel" if verified else "source-checkout",
        artifact_digest="a" * 64,
        runner_version="0.2.0",
        verification_authority="wheel-sha256" if verified else "none",
        policy_digest="b" * 64,
    )


def _validate(root: Path, trusted: object | None = None, authority=None):
    from zagrosi_forge.install.metadata import validate_package

    return validate_package(
        root,
        runner=_runner(),
        trusted=_trusted() if trusted is None else trusted,
        path_authority=PlatformPathAuthority() if authority is None else authority,
    )


def _load_json(root: Path, relative: str) -> Any:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _write_json(root: Path, relative: str, value: object) -> None:
    (root / relative).write_text(json.dumps(value), encoding="utf-8")


def _assert_failure(result: object, code: str) -> None:
    assert not result.is_ok
    assert result.error is not None
    assert result.error.code == code
    expected_category = (
        15
        if code == "runner.untrusted"
        else 11
        if code in {"metadata.reference_missing", "metadata.reference_type"}
        else 10
    )
    assert result.error.exit_category == expected_category
    assert result.value is None


def test_current_package_validates_to_normalized_contract(tmp_path: Path) -> None:
    root = _package_factory(tmp_path)
    result = _validate(root)

    assert result.is_ok
    package = result.unwrap()
    assert package.plugin.name == "zagrosi-forge"
    assert package.plugin.version == "0.2.0"
    assert package.marketplace.name == "zagrosi"
    assert package.marketplace.selected_plugin == "zagrosi-forge"
    assert package.base_release_version == "0.2.0"
    reference_projection = tuple(_relative_text(item) for item in package.references)
    assert reference_projection == tuple(sorted(reference_projection))
    assert set(_REQUIRED_REGULAR) <= set(reference_projection)
    assert (
        tuple(_relative_text(item) for item in package.source_snapshot.references)
        == reference_projection
    )
    package.source_snapshot.close()

    from zagrosi_forge.install.metadata import validate_package
    from zagrosi_forge.install.paths import PlatformPathAuthority

    for candidate in (root, _PROJECT_ROOT):
        native = validate_package(
            candidate,
            runner=_runner(),
            trusted=_trusted(),
            path_authority=PlatformPathAuthority(),
        )
        assert native.is_ok
        native.unwrap().source_snapshot.close()


def test_snapshot_never_reopens_replaced_candidate_path(
    tmp_path: Path,
) -> None:
    root = _package_factory(tmp_path)
    result = _validate(root)

    assert result.is_ok
    package = result.unwrap()
    manifest_reference = next(
        reference for reference in package.references if reference.value == _MANIFEST
    )
    replacement = _load_json(root, _MANIFEST)
    replacement["description"] = "replacement after validation"
    temporary = root / ".codex-plugin/plugin.swap"
    _write_json(root, ".codex-plugin/plugin.swap", replacement)
    os.replace(temporary, root / _MANIFEST)

    with pytest.raises(ForgeError) as caught:
        package.source_snapshot.read_bytes(manifest_reference, limit=256 * 1024)
    assert caught.value.code == "path.identity_changed"
    assert _load_json(root, _MANIFEST)["description"] == replacement["description"]
    package.source_snapshot.close()


def test_authoritative_package_retains_sealed_snapshot_handles(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import OpenedRegularFile, SourceSnapshot

    root = _package_factory(tmp_path)
    package = _validate(root).unwrap()
    manifest_reference = next(
        reference for reference in package.references if reference.value == _MANIFEST
    )

    assert isinstance(package.source_snapshot, SourceSnapshot)
    adopted = package.source_snapshot.file(manifest_reference)
    assert isinstance(adopted, OpenedRegularFile)
    assert adopted.read_bytes(limit=256 * 1024)
    package.source_snapshot.close()
    with pytest.raises(ForgeError, match="opened file capability is closed"):
        adopted.read_bytes(limit=256 * 1024)


def test_snapshot_identity_binds_root_file_and_content_identity(tmp_path: Path) -> None:
    root_a = _package_factory(tmp_path, "root-a")
    root_b = _package_factory(tmp_path, "root-b")
    first = _validate(root_a).unwrap()
    unchanged = _validate(root_a).unwrap()
    other_root = _validate(root_b).unwrap()

    assert first.source_snapshot_identity == unchanged.source_snapshot_identity
    assert first.source_snapshot_identity != other_root.source_snapshot_identity
    first.source_snapshot.close()
    unchanged.source_snapshot.close()
    other_root.source_snapshot.close()

    script = root_a / "scripts/zagrosi_skills.py"
    script.write_text('VALUE = "changed"\n', encoding="utf-8")
    changed_content = _validate(root_a).unwrap()
    assert changed_content.source_snapshot_identity != first.source_snapshot_identity
    changed_content.source_snapshot.close()

    before = script.stat(follow_symlinks=False)
    script.write_bytes(b'VALUE = "altered"\n')
    after = script.stat(follow_symlinks=False)
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    changed_same_file = _validate(root_a).unwrap()
    assert (
        changed_same_file.source_snapshot_identity
        != changed_content.source_snapshot_identity
    )
    changed_same_file.source_snapshot.close()


def test_oversize_opened_metadata_maps_to_metadata_too_large(tmp_path: Path) -> None:
    from zagrosi_forge.install.metadata import validate_package
    from zagrosi_forge.install.paths import PlatformPathAuthority

    root = _package_factory(tmp_path)
    (root / _MANIFEST).write_bytes(b"x" * (LIMIT_POLICY.value("json_record_bytes") + 1))

    result = validate_package(
        root,
        runner=_runner(),
        trusted=_trusted(),
        path_authority=PlatformPathAuthority(),
    )
    _assert_failure(result, "metadata.too_large")


def test_empty_plugin_object_fails_without_false_score(tmp_path: Path) -> None:
    root = _package_factory(tmp_path)
    _write_json(root, _MANIFEST, {})
    _assert_failure(_validate(root), "metadata.schema")


def test_marketplace_array_fails_without_traceback(tmp_path: Path) -> None:
    root = _package_factory(tmp_path)
    _write_json(root, _MARKETPLACE, [])
    result = _validate(root)
    _assert_failure(result, "metadata.root_type")
    assert result.error.__traceback__ is None


def test_duplicate_json_key_is_rejected(tmp_path: Path) -> None:
    root = _package_factory(tmp_path)
    raw = (root / _MANIFEST).read_text(encoding="utf-8")
    (root / _MANIFEST).write_text(
        raw.replace(
            '"name": "zagrosi-forge",', '"name":"other","name":"zagrosi-forge",'
        ),
        encoding="utf-8",
    )
    _assert_failure(_validate(root), "metadata.duplicate_key")

    invalid_utf8 = _package_factory(tmp_path, "invalid-utf8")
    (invalid_utf8 / _MANIFEST).write_bytes(b"\xff")
    _assert_failure(_validate(invalid_utf8), "metadata.invalid_utf8")

    nested: object = "leaf"
    for _ in range(LIMIT_POLICY.value("json_depth") + 1):
        nested = {"nested": nested}
    too_deep = _package_factory(tmp_path, "too-deep")
    _write_json(too_deep, _MANIFEST, nested)
    _assert_failure(_validate(too_deep), "metadata.too_large")

    too_many = _package_factory(tmp_path, "too-many-members")
    _write_json(
        too_many,
        _MANIFEST,
        {
            f"member-{index}": index
            for index in range(LIMIT_POLICY.value("json_members") + 1)
        },
    )
    _assert_failure(_validate(too_many), "metadata.too_large")


def test_unknown_or_wrong_type_field_is_rejected(tmp_path: Path) -> None:
    for index, (mutation, removed, code) in enumerate(
        (
            ({"unknown": True}, None, "metadata.unknown_field"),
            ({"description": 7}, None, "metadata.schema"),
            ({"description": None}, None, "metadata.schema"),
            ({"description": False}, None, "metadata.schema"),
            ({"description": ""}, None, "metadata.schema"),
            ({}, "description", "metadata.schema"),
        )
    ):
        root = _package_factory(tmp_path, f"candidate-{index}")
        plugin = _load_json(root, _MANIFEST)
        plugin.update(mutation)
        if removed is not None:
            del plugin[removed]
        _write_json(root, _MANIFEST, plugin)
        _assert_failure(_validate(root), code)


def test_release_semver_and_pyproject_parity_are_strict(tmp_path: Path) -> None:
    for index, version in enumerate(("0.2", "00.2.0", "0.2.0+local")):
        root = _package_factory(tmp_path, f"invalid-version-{index}")
        plugin = _load_json(root, _MANIFEST)
        plugin["version"] = version
        _write_json(root, _MANIFEST, plugin)
        _assert_failure(_validate(root), "metadata.version")

    root = _package_factory(tmp_path, "mismatch")
    (root / _PROJECT).write_text(
        '[project]\nname = "zagrosi-forge"\nversion = "0.2.1"\n',
        encoding="utf-8",
    )
    _assert_failure(_validate(root), "metadata.version_mismatch")


def test_marketplace_requires_one_exact_selected_entry(tmp_path: Path) -> None:
    cases: tuple[tuple[str, Any, str], ...] = (
        ("absent", [], "metadata.selected_plugin"),
        (
            "duplicate",
            None,
            "metadata.duplicate_plugin",
        ),
        ("unsafe", "/tmp/candidate", "metadata.reference_unsafe"),
    )
    for name, replacement, code in cases:
        root = _package_factory(tmp_path, name)
        marketplace = _load_json(root, _MARKETPLACE)
        if name == "absent":
            marketplace["plugins"] = replacement
        elif name == "duplicate":
            marketplace["plugins"].append(dict(marketplace["plugins"][0]))
        else:
            marketplace["plugins"][0]["source"]["path"] = replacement
        _write_json(root, _MARKETPLACE, marketplace)
        _assert_failure(_validate(root), code)


def test_every_skill_asset_schema_vendor_and_notice_reference_is_regular(
    tmp_path: Path,
) -> None:
    target_relative = "skills/zagrosi-implement/SKILL.md"
    for case in ("missing", "directory", "symlink", "hardlink"):
        root = _package_factory(tmp_path, case)
        target = root / target_relative
        target.unlink()
        if case == "directory":
            target.mkdir()
        elif case == "symlink":
            target.symlink_to(root / "NOTICE.md")
        elif case == "hardlink":
            os.link(root / "NOTICE.md", target)
        result = _validate(root)
        _assert_failure(
            result,
            "metadata.reference_missing"
            if case == "missing"
            else "metadata.reference_type",
        )


def test_candidate_policy_cannot_expand_trusted_bundle_authority(
    tmp_path: Path,
) -> None:
    root = _package_factory(tmp_path)
    (root / "src/zagrosi_forge/install/limit-policy.json").write_text(
        '{"policy_version":"2.0","references":["evil.py"]}\n', encoding="utf-8"
    )
    (root / "evil.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    result = _validate(root)

    _assert_failure(result, "package.runner_upgrade_required")


def test_candidate_asset_cannot_expand_installed_bundle_read_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zagrosi_forge.install.paths import SourceRoot

    root = _package_factory(tmp_path)
    plugin = _load_json(root, _MANIFEST)
    plugin["interface"]["screenshots"].append("./candidate-only/asset.svg")
    _write_json(root, _MANIFEST, plugin)
    candidate = root / "candidate-only/asset.svg"
    candidate.parent.mkdir()
    candidate.write_bytes(b"ZAGROSI_CANDIDATE_ONLY_ASSET\n")
    opened: list[str] = []
    original_open = SourceRoot.open_regular_file

    def observe_open(source: SourceRoot, reference: object):
        opened.append(_relative_text(reference))
        return original_open(source, reference)  # type: ignore[arg-type]

    monkeypatch.setattr(SourceRoot, "open_regular_file", observe_open)
    result = _validate(root)

    _assert_failure(result, "metadata.reference_unsafe")
    assert "candidate-only/asset.svg" not in opened


def test_validated_snapshot_covers_only_installed_bundle_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import validate_reference

    trusted = _trusted()
    bundle_members = trusted.bundle_member_references
    validation_only = trusted.bundle_validation_references

    assert bundle_members == tuple(sorted(set(bundle_members)))
    assert validation_only == tuple(sorted(set(validation_only)))
    assert set(bundle_members).isdisjoint(validation_only)
    assert {
        _MANIFEST,
        _PROJECT,
        "LICENSE",
        "NOTICE.md",
        "README.md",
        "scripts/deep_skills.py",
        "scripts/zagrosi_skills.py",
        "src/zagrosi_forge/install/metadata.py",
        "src/zagrosi_forge/_vendor/tomlkit-LICENSE",
    } <= set(bundle_members)
    assert {_MARKETPLACE, ".codexignore"} <= set(validation_only)

    root = _package_factory(tmp_path)
    expected = tuple(sorted({*bundle_members, *validation_only}))
    for relative in expected:
        destination = root / relative
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_PROJECT_ROOT / relative, destination)

    candidate_only = "candidate-only/expand.py"
    (root / candidate_only).parent.mkdir()
    (root / candidate_only).write_text(
        "raise AssertionError('candidate file was opened')\n", encoding="utf-8"
    )

    package = _validate(root, trusted=trusted).unwrap()
    try:
        package_references = tuple(
            _relative_text(reference) for reference in package.references
        )
        snapshot_references = tuple(
            _relative_text(reference)
            for reference in package.source_snapshot.references
        )
        assert package_references == expected
        assert snapshot_references == expected
        assert candidate_only not in package_references
        candidate_reference = validate_reference(
            candidate_only, role="candidate-only", limits=LIMIT_POLICY
        ).unwrap()
        with pytest.raises(ForgeError) as outside_snapshot:
            package.source_snapshot.file(candidate_reference)
        assert outside_snapshot.value.code == "path.outside_root"
    finally:
        package.source_snapshot.close()


def _schema_accepts(schema: dict[str, Any], value: object) -> bool:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            tuple(str(item) for item in error.absolute_schema_path),
        ),
    )
    return not errors


def _changed(document: object, path: tuple[object, ...], value: object) -> object:
    rendered = json.loads(json.dumps(document))
    target = rendered
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    return rendered


def _runtime_code(result: Any) -> str | None:
    if result.is_ok:
        result.unwrap().source_snapshot.close()
        return None
    return result.error.code


def test_schema_and_runtime_adversarial_corpus_have_parity(tmp_path: Path) -> None:
    plugin_schema = json.loads(
        (_SCHEMAS / "plugin-v1.schema.json").read_text(encoding="utf-8")
    )
    marketplace_schema = json.loads(
        (_SCHEMAS / "marketplace-v1.schema.json").read_text(encoding="utf-8")
    )
    plugin = json.loads((_FIXTURE / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (_FIXTURE / "marketplace.json").read_text(encoding="utf-8")
    )
    duplicate_marketplace = json.loads(json.dumps(marketplace))
    duplicate_marketplace["plugins"].append(
        json.loads(json.dumps(duplicate_marketplace["plugins"][0]))
    )
    other_marketplace = _changed(marketplace, ("plugins", 0, "name"), "another-plugin")
    corpus: tuple[tuple[str, dict[str, Any], str, object, str | None], ...] = (
        ("plugin-valid", plugin_schema, _MANIFEST, plugin, None),
        ("plugin-empty", plugin_schema, _MANIFEST, {}, "metadata.schema"),
        ("plugin-root", plugin_schema, _MANIFEST, [], "metadata.root_type"),
        (
            "plugin-unknown",
            plugin_schema,
            _MANIFEST,
            {**plugin, "unknown": True},
            "metadata.unknown_field",
        ),
        (
            "plugin-type",
            plugin_schema,
            _MANIFEST,
            _changed(plugin, ("description",), 7),
            "metadata.schema",
        ),
        (
            "plugin-author-unknown",
            plugin_schema,
            _MANIFEST,
            _changed(plugin, ("author", "unknown"), True),
            "metadata.unknown_field",
        ),
        (
            "plugin-author-empty",
            plugin_schema,
            _MANIFEST,
            _changed(plugin, ("author", "name"), ""),
            "metadata.schema",
        ),
        (
            "plugin-keywords-duplicate",
            plugin_schema,
            _MANIFEST,
            _changed(plugin, ("keywords",), ["same", "same"]),
            "metadata.schema",
        ),
        (
            "plugin-interface-empty",
            plugin_schema,
            _MANIFEST,
            _changed(plugin, ("interface", "capabilities"), []),
            "metadata.schema",
        ),
        (
            "plugin-interface-missing",
            plugin_schema,
            _MANIFEST,
            {**plugin, "interface": {}},
            "metadata.schema",
        ),
        (
            "plugin-version",
            plugin_schema,
            _MANIFEST,
            _changed(plugin, ("version",), "00.2.0"),
            "metadata.version",
        ),
        ("market-valid", marketplace_schema, _MARKETPLACE, marketplace, None),
        ("market-empty", marketplace_schema, _MARKETPLACE, {}, "metadata.schema"),
        ("market-root", marketplace_schema, _MARKETPLACE, [], "metadata.root_type"),
        (
            "market-unknown",
            marketplace_schema,
            _MARKETPLACE,
            {**marketplace, "unknown": True},
            "metadata.unknown_field",
        ),
        (
            "market-interface-unknown",
            marketplace_schema,
            _MARKETPLACE,
            _changed(marketplace, ("interface", "unknown"), True),
            "metadata.unknown_field",
        ),
        (
            "market-plugin-type",
            marketplace_schema,
            _MARKETPLACE,
            _changed(marketplace, ("plugins",), "bad"),
            "metadata.schema",
        ),
        (
            "market-plugin-empty",
            marketplace_schema,
            _MARKETPLACE,
            _changed(marketplace, ("plugins",), []),
            "metadata.selected_plugin",
        ),
        (
            "market-plugin-duplicate",
            marketplace_schema,
            _MARKETPLACE,
            duplicate_marketplace,
            "metadata.duplicate_plugin",
        ),
        (
            "market-source-unsafe",
            marketplace_schema,
            _MARKETPLACE,
            _changed(marketplace, ("plugins", 0, "source", "path"), "/tmp/plugin"),
            "metadata.reference_unsafe",
        ),
        (
            "market-policy",
            marketplace_schema,
            _MARKETPLACE,
            _changed(
                marketplace,
                ("plugins", 0, "policy", "authentication"),
                "NEVER",
            ),
            "metadata.schema",
        ),
        (
            "market-source-unknown",
            marketplace_schema,
            _MARKETPLACE,
            _changed(marketplace, ("plugins", 0, "source", "unknown"), True),
            "metadata.unknown_field",
        ),
        (
            "market-selected-absent",
            marketplace_schema,
            _MARKETPLACE,
            other_marketplace,
            "metadata.selected_plugin",
        ),
    )

    for name, schema, relative, candidate, expected_code in corpus:
        root = _package_factory(tmp_path, f"corpus-{name}")
        assert _schema_accepts(schema, candidate) == (expected_code is None)
        _write_json(root, relative, candidate)
        result = _validate(root)
        assert _runtime_code(result) == expected_code


def test_runtime_and_schema_agree_on_skills_and_scripts_profile(tmp_path: Path) -> None:
    schema = json.loads(
        (_SCHEMAS / "plugin-v1.schema.json").read_text(encoding="utf-8")
    )
    valid = json.loads((_FIXTURE / "plugin.json").read_text(encoding="utf-8"))
    cases: tuple[tuple[str, object, str | None], ...] = (
        ("skills", "./skills/", None),
        ("scripts", "./scripts/", None),
        ("skills", "skills/", "metadata.reference_unsafe"),
        ("skills", "./skills", "metadata.reference_unsafe"),
        ("scripts", "scripts/", "metadata.reference_unsafe"),
        ("scripts", "../scripts/", "metadata.reference_unsafe"),
        ("scripts", False, "metadata.schema"),
    )

    for index, (field, value, expected_code) in enumerate(cases):
        candidate = _changed(valid, (field,), value)
        assert _schema_accepts(schema, candidate) == (expected_code is None)
        root = _package_factory(tmp_path, f"profile-{index}")
        _write_json(root, _MANIFEST, candidate)
        result = _validate(root)
        assert _runtime_code(result) == expected_code


@pytest.mark.parametrize(
    ("reference", "accepted"),
    (
        ("./assets/icon.svg", True),
        ("./assets/CON.svg", False),
        ("./assets/file.", False),
        ("./assets/é.svg", False),
        ("./assets/icon.svg\n", False),
        (_ASSET_AT_NORMALIZED_BYTE_LIMIT, False),
    ),
)
def test_asset_reference_schema_and_runtime_grammar_have_parity(
    tmp_path: Path, reference: str, accepted: bool
) -> None:
    schema = json.loads(
        (_SCHEMAS / "plugin-v1.schema.json").read_text(encoding="utf-8")
    )
    plugin = json.loads((_FIXTURE / "plugin.json").read_text(encoding="utf-8"))
    candidate = _changed(plugin, ("interface", "composerIcon"), reference)
    root = _package_factory(tmp_path)
    if accepted:
        (root / reference.removeprefix("./")).write_bytes(b"<svg/>\n")
    _write_json(root, _MANIFEST, candidate)

    assert _schema_accepts(schema, candidate) is accepted
    result = _validate(root)
    if accepted:
        assert result.is_ok
        result.unwrap().source_snapshot.close()
    else:
        _assert_failure(result, "metadata.reference_unsafe")


@pytest.mark.parametrize(("length", "accepted"), ((128, True), (129, False)))
def test_unicode_string_schema_and_runtime_length_have_parity(
    tmp_path: Path, length: int, accepted: bool
) -> None:
    schema = json.loads(
        (_SCHEMAS / "plugin-v1.schema.json").read_text(encoding="utf-8")
    )
    plugin = json.loads((_FIXTURE / "plugin.json").read_text(encoding="utf-8"))
    plugin["author"]["name"] = "é" * length
    root = _package_factory(tmp_path)
    _write_json(root, _MANIFEST, plugin)

    assert _schema_accepts(schema, plugin) is accepted
    result = _validate(root)
    assert result.is_ok is accepted
    if result.is_ok:
        result.unwrap().source_snapshot.close()


def test_escaped_surrogate_schema_and_runtime_have_parity(tmp_path: Path) -> None:
    schema = json.loads(
        (_SCHEMAS / "plugin-v1.schema.json").read_text(encoding="utf-8")
    )
    plugin = json.loads((_FIXTURE / "plugin.json").read_text(encoding="utf-8"))
    plugin["author"]["name"] = "\ud800"
    root = _package_factory(tmp_path)
    _write_json(root, _MANIFEST, plugin)

    assert not _schema_accepts(schema, plugin)
    _assert_failure(_validate(root), "metadata.schema")


def test_metadata_reference_set_rejects_portable_collision_before_open(
    tmp_path: Path,
) -> None:
    schema = json.loads(
        (_SCHEMAS / "plugin-v1.schema.json").read_text(encoding="utf-8")
    )
    plugin = json.loads((_FIXTURE / "plugin.json").read_text(encoding="utf-8"))
    plugin["interface"]["composerIcon"] = "./assets/Icon.svg"
    plugin["interface"]["screenshots"].append("./assets/icon.svg")
    root = _package_factory(tmp_path)
    (root / "assets/Icon.svg").write_bytes(b"<svg/>\n")
    _write_json(root, _MANIFEST, plugin)
    assert _schema_accepts(schema, plugin)
    result = _validate(root)

    _assert_failure(result, "metadata.reference_unsafe")


def test_findings_are_stably_sorted_and_path_redacted(tmp_path: Path) -> None:
    root = _package_factory(tmp_path)
    plugin = _load_json(root, _MANIFEST)
    plugin["TOKEN=/Users/alice/private"] = True
    marketplace = _load_json(root, _MARKETPLACE)
    marketplace["password=C:\\private"] = True
    _write_json(root, _MANIFEST, plugin)
    _write_json(root, _MARKETPLACE, marketplace)

    first = _validate(root)
    second = _validate(root)
    first_keys = tuple((item.subject, item.code) for item in first.findings)
    second_keys = tuple((item.subject, item.code) for item in second.findings)
    rendered = repr(first.error.safe_message) + repr(first.findings)

    assert first_keys == tuple(sorted(first_keys)) == second_keys
    assert str(root) not in rendered
    assert "alice" not in rendered
    assert "private" not in rendered
    assert "password" not in rendered


def test_structural_path_fake_cannot_mint_authoritative_package(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.metadata import validate_package

    root = _package_factory(tmp_path)
    authority = _StructuralPathAuthorityFake()

    result = validate_package(
        root,
        runner=_runner(),
        trusted=_trusted(),
        path_authority=authority,
    )

    _assert_failure(result, "metadata.reference_type")
    assert authority.opened_roots == []
    assert authority.opened == []


def test_validated_package_cannot_be_constructed_outside_validator(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.metadata import ValidatedPackage

    package = _validate(_package_factory(tmp_path)).unwrap()
    with pytest.raises(TypeError, match="created only by validate_package"):
        ValidatedPackage(
            plugin=package.plugin,
            marketplace=package.marketplace,
            base_release_version="9.9.9",
            references=package.references,
            trusted_policy_digests=package.trusted_policy_digests,
            source_snapshot_identity=package.source_snapshot_identity,
            findings=(),
            source_snapshot=package.source_snapshot,
        )
    package.source_snapshot.close()


def test_authoritative_validation_rejects_untrusted_runner_before_candidate_read(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.metadata import validate_package

    root = _package_factory(tmp_path)
    authority = _StructuralPathAuthorityFake()
    result = validate_package(
        root,
        runner=_runner(verified=False),
        trusted=_trusted(),
        path_authority=authority,
    )

    _assert_failure(result, "runner.untrusted")
    assert authority.opened_roots == []
    assert authority.opened == []


def test_untrusted_inspection_reports_bounded_invalid_metadata_without_capability(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.metadata import inspect_package_untrusted

    root = _package_factory(tmp_path)
    _write_json(root, _MANIFEST, [])
    report = inspect_package_untrusted(
        root,
        runner=_runner(verified=False),
        trusted=_trusted(),
        path_authority=PlatformPathAuthority(),
    )

    assert isinstance(report, DiagnosticReport)
    assert not report.authoritative
    assert not report.is_valid
    assert {finding.code for finding in report.findings} == {"metadata.root_type"}
    assert all(
        len(finding.message.encode("utf-8")) <= 1_024 for finding in report.findings
    )
    assert all(str(root) not in repr(finding) for finding in report.findings)
    assert not hasattr(report, "value")
    assert not hasattr(report, "source_snapshot")
    assert not hasattr(report, "unwrap")


def test_validation_never_imports_or_executes_candidate_code(tmp_path: Path) -> None:
    root = _package_factory(tmp_path)
    marker = tmp_path / "candidate-executed"
    writer = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
    (root / "scripts/fixture.py").write_text(writer, encoding="utf-8")
    for relative in (
        "sitecustomize.py",
        "usercustomize.py",
        "path_hook.py",
        "src/zagrosi_forge/__init__.py",
        "zagrosi_forge-999.0.dist-info/entry_points.txt",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(writer, encoding="utf-8")
    before = tuple(sys.path)

    result = _validate(root)

    assert result.is_ok
    result.unwrap().source_snapshot.close()
    assert not marker.exists()
    assert tuple(sys.path) == before
    assert str(root) not in sys.path
