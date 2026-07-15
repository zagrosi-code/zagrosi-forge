"""Candidate metadata decoding without candidate import or execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib import resources
import json
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import tomllib
from types import MappingProxyType
from typing import Any, Iterable, Mapping, cast

from .contracts import (
    DiagnosticReport,
    Finding,
    ForgeError,
    RunnerOperation,
    RunnerProvenance,
    ValidationResult,
    canonical_json_bytes,
    parse_release_version,
    require_runner_authority,
)
from .policies import LIMIT_POLICY, LimitPolicy
from .paths import (
    OpenedRegularFile,
    PlatformPathAuthority,
    SafeRelativePath,
    SourceRoot,
    SourceSnapshot,
    validate_reference,
    validate_reference_set,
)


_PLUGIN_FILE = ".codex-plugin/plugin.json"
_MARKETPLACE_FILE = ".agents/plugins/marketplace.json"
_PROJECT_FILE = "pyproject.toml"
_TRUSTED_AUTHORITY_TOKEN = object()
_VALIDATED_PACKAGE_TOKEN = object()
_INSTALLED_POLICY_RESOURCES = (
    "limit-policy.json",
    "native-isolation-policy.json",
    "native-support-policy.json",
    "recovery-retention-policy.json",
    "toolchain-lock.json",
)
_INSTALLED_SCHEMA_RESOURCES = (
    "limit-policy-v1.schema.json",
    "marketplace-v1.schema.json",
    "native-isolation-policy-v1.schema.json",
    "native-support-policy-v1.schema.json",
    "ownership-receipt-v1.schema.json",
    "plugin-v1.schema.json",
    "recovery-retention-policy-v1.schema.json",
    "toolchain-lock-v1.schema.json",
    "vendor-receipt-v1.schema.json",
)
_INSTALLED_AUTHORITY_RESOURCES = (
    *(
        (
            f"src/zagrosi_forge/install/{resource}",
            "zagrosi_forge.install",
            resource,
        )
        for resource in _INSTALLED_POLICY_RESOURCES
    ),
    *(
        (
            f"src/zagrosi_forge/install/schemas/{resource}",
            "zagrosi_forge.install",
            f"schemas/{resource}",
        )
        for resource in _INSTALLED_SCHEMA_RESOURCES
    ),
    (
        "src/zagrosi_forge/_vendor/vendor-receipt.json",
        "zagrosi_forge._vendor",
        "vendor-receipt.json",
    ),
)
_TRUSTED_REQUIRED_REGULAR_REFERENCES = (
    "NOTICE.md",
    "assets/icon.svg",
    "assets/readme-hero.svg",
    "assets/readme-workflow.svg",
    "scripts/deep_skills.py",
    "scripts/zagrosi_skills.py",
    "skills/zagrosi-implement/SKILL.md",
    "skills/zagrosi-implement/agents/openai.yaml",
    "skills/zagrosi-implement/references/quality-gates.md",
    "skills/zagrosi-implement/references/section-update.md",
    "skills/zagrosi-implement/references/tdd-review-git.md",
    "skills/zagrosi-plan/SKILL.md",
    "skills/zagrosi-plan/agents/openai.yaml",
    "skills/zagrosi-plan/references/depth-standards.md",
    "skills/zagrosi-plan/references/domain-ai-products.md",
    "skills/zagrosi-plan/references/domain-auth.md",
    "skills/zagrosi-plan/references/domain-data-migration.md",
    "skills/zagrosi-plan/references/domain-frontend.md",
    "skills/zagrosi-plan/references/domain-infra.md",
    "skills/zagrosi-plan/references/domain-payments.md",
    "skills/zagrosi-plan/references/evaluation.md",
    "skills/zagrosi-plan/references/plan-format.md",
    "skills/zagrosi-plan/references/quality-gates.md",
    "skills/zagrosi-plan/references/research.md",
    "skills/zagrosi-plan/references/review.md",
    "skills/zagrosi-plan/references/section-format.md",
    "skills/zagrosi-plan/references/workflow-contract.md",
    "skills/zagrosi-project/SKILL.md",
    "skills/zagrosi-project/agents/openai.yaml",
    "skills/zagrosi-project/references/interview.md",
    "skills/zagrosi-project/references/manifest-format.md",
    "skills/zagrosi-project/references/spec-format.md",
    "skills/zagrosi-project/references/splitting.md",
    "skills/zagrosi-project/references/workflow-contract.md",
)
_IDENTIFIER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ASSET_COMPONENT_PATTERN = (
    r"(?!(?:[Cc][Oo][Nn]|[Pp][Rr][Nn]|[Aa][Uu][Xx]|[Nn][Uu][Ll]|"
    r"[Cc][Ll][Oo][Cc][Kk]\$|[Cc][Oo][Mm][1-9]|[Ll][Pp][Tt][1-9])"
    r"(?:\.|/|$))"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?"
)
_ASSET_REFERENCE = re.compile(
    rf"{_ASSET_COMPONENT_PATTERN}(?:/{_ASSET_COMPONENT_PATTERN})*\Z"
)
_PLUGIN_FIELDS = frozenset(
    {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "skills",
        "scripts",
        "interface",
    }
)
_AUTHOR_FIELDS = frozenset({"name", "url"})
_INTERFACE_FIELDS = frozenset(
    {
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "composerIcon",
        "websiteURL",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "defaultPrompt",
        "brandColor",
        "screenshots",
    }
)
_MARKETPLACE_FIELDS = frozenset({"name", "interface", "plugins"})
_MARKETPLACE_INTERFACE_FIELDS = frozenset({"displayName"})
_ENTRY_FIELDS = frozenset({"name", "source", "policy", "category"})
_SOURCE_FIELDS = frozenset({"source", "path"})
_POLICY_FIELDS = frozenset({"installation", "authentication"})
_SAFE_MESSAGES = {
    "metadata.too_large": "Candidate metadata exceeds the trusted limit.",
    "metadata.invalid_utf8": "Candidate metadata is not valid UTF-8.",
    "metadata.duplicate_key": "Candidate metadata contains a duplicate key.",
    "metadata.root_type": "Candidate metadata root must be an object.",
    "metadata.schema": "Candidate metadata does not match the trusted schema.",
    "metadata.unknown_field": "Candidate metadata contains an unsupported field.",
    "metadata.version": "Candidate release version is not strict SemVer.",
    "metadata.version_mismatch": "Candidate release versions do not match.",
    "metadata.duplicate_plugin": "Marketplace plugin entries are not unique.",
    "metadata.selected_plugin": "Marketplace must select exactly one Forge plugin.",
    "metadata.reference_unsafe": "Candidate metadata contains an unsafe reference.",
    "metadata.reference_missing": "A trusted required reference is missing.",
    "metadata.reference_type": "A trusted required reference is not a regular file.",
    "metadata.policy_mismatch": "Candidate authority metadata does not match.",
    "package.runner_upgrade_required": "Candidate policy requires a compatible runner.",
    "path.identity_changed": "Candidate identity changed during validation.",
    "runner.untrusted": "The current runner cannot validate a candidate.",
}


@dataclass(frozen=True, slots=True, init=False)
class TrustedPolicySet:
    """Installed authority inputs; candidate copies can only match these values."""

    limits: LimitPolicy
    authority_version: str
    authority_file_digests: Mapping[str, str]
    required_regular_references: tuple[str, ...]
    _authority_token: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        limits: LimitPolicy,
        authority_version: str,
        authority_file_digests: Mapping[str, str],
        required_regular_references: tuple[str, ...],
        _token: object,
    ) -> None:
        if _token is not _TRUSTED_AUTHORITY_TOKEN:
            raise TypeError(
                "TrustedPolicySet is created only from installed package resources"
            )
        object.__setattr__(self, "limits", limits)
        object.__setattr__(self, "authority_version", authority_version)
        object.__setattr__(self, "authority_file_digests", authority_file_digests)
        object.__setattr__(
            self, "required_regular_references", required_regular_references
        )
        object.__setattr__(self, "_authority_token", _token)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.limits, LimitPolicy) or self.authority_version != "1.0":
            raise ValueError("trusted metadata authority is invalid")
        digests = dict(self.authority_file_digests)
        references = tuple(self.required_regular_references)
        if not digests or any(
            not isinstance(path, str)
            or not _is_safe_reference(path, self.limits)
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
            for path, digest in digests.items()
        ):
            raise ValueError("trusted authority file digest is invalid")
        if (
            not references
            or len(set(references)) != len(references)
            or any(not _is_safe_reference(path, self.limits) for path in references)
        ):
            raise ValueError("trusted regular reference set is invalid")
        object.__setattr__(
            self,
            "authority_file_digests",
            MappingProxyType(dict(sorted(digests.items()))),
        )
        object.__setattr__(
            self, "required_regular_references", tuple(sorted(references))
        )


def _installed_resource_bytes(package: str, relative: str) -> bytes:
    resource = resources.files(package)
    for component in relative.split("/"):
        resource = resource.joinpath(component)
    return resource.read_bytes()


def load_installed_trusted_policy_set() -> TrustedPolicySet:
    """Load the fixed metadata authority from the running distribution only."""

    digests = {
        candidate_path: hashlib.sha256(
            _installed_resource_bytes(package, resource)
        ).hexdigest()
        for candidate_path, package, resource in _INSTALLED_AUTHORITY_RESOURCES
    }
    return TrustedPolicySet(
        limits=LIMIT_POLICY,
        authority_version="1.0",
        authority_file_digests=digests,
        required_regular_references=_TRUSTED_REQUIRED_REGULAR_REFERENCES,
        _token=_TRUSTED_AUTHORITY_TOKEN,
    )


def _is_installed_trusted_policy_set(value: object) -> bool:
    return (
        isinstance(value, TrustedPolicySet)
        and getattr(value, "_authority_token", None) is _TRUSTED_AUTHORITY_TOKEN
    )


def _is_sealed_path_authority(value: object) -> bool:
    """Accept only the concrete facade that owns the native capability origin."""

    return type(value) is PlatformPathAuthority


@dataclass(frozen=True, slots=True)
class PluginContract:
    schema_version: str
    compatibility_authority: str
    name: str
    version: str
    description: str
    author_name: str
    author_url: str
    homepage: str
    repository: str
    license: str
    keywords: tuple[str, ...]
    skills_root: str
    scripts_root: str
    asset_references: tuple[str, ...]
    source_manifest_digest: str


@dataclass(frozen=True, slots=True)
class MarketplaceEntry:
    name: str
    source_kind: str
    source_path: str
    category: str
    installation_policy: str
    authentication_policy: str


@dataclass(frozen=True, slots=True)
class MarketplaceContract:
    schema_version: str
    name: str
    display_name: str
    plugins: tuple[MarketplaceEntry, ...]
    selected_plugin: str
    source_manifest_digest: str


@dataclass(frozen=True, slots=True, init=False)
class ValidatedPackage:
    plugin: PluginContract
    marketplace: MarketplaceContract
    base_release_version: str
    references: tuple[SafeRelativePath, ...]
    trusted_policy_digests: Mapping[str, str]
    source_snapshot_identity: str
    findings: tuple[Finding, ...]
    source_snapshot: SourceSnapshot

    def __init__(
        self,
        *,
        plugin: PluginContract,
        marketplace: MarketplaceContract,
        base_release_version: str,
        references: tuple[SafeRelativePath, ...],
        trusted_policy_digests: Mapping[str, str],
        source_snapshot_identity: str,
        findings: tuple[Finding, ...],
        source_snapshot: SourceSnapshot,
        _token: object | None = None,
    ) -> None:
        if _token is not _VALIDATED_PACKAGE_TOKEN:
            raise TypeError("ValidatedPackage is created only by validate_package")
        object.__setattr__(self, "plugin", plugin)
        object.__setattr__(self, "marketplace", marketplace)
        object.__setattr__(self, "base_release_version", base_release_version)
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "trusted_policy_digests", trusted_policy_digests)
        object.__setattr__(self, "source_snapshot_identity", source_snapshot_identity)
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "source_snapshot", source_snapshot)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.source_snapshot, SourceSnapshot):
            raise TypeError("ValidatedPackage requires a sealed source snapshot")
        object.__setattr__(
            self,
            "trusted_policy_digests",
            MappingProxyType(dict(sorted(self.trusted_policy_digests.items()))),
        )


class _DuplicateKey(ValueError):
    pass


def _string(value: object, *, maximum: int = 4_096) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return len(value) <= maximum


def _is_release_version(value: object) -> bool:
    try:
        parse_release_version(value)
    except ValueError:
        return False
    return True


def _string_list(
    value: object, *, maximum_items: int, maximum_bytes: int
) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum_items:
        return None
    if any(not _string(item, maximum=maximum_bytes) for item in value):
        return None
    rendered = cast(tuple[str, ...], tuple(value))
    if len(set(rendered)) != len(rendered):
        return None
    return rendered


def _mapping_fields(
    value: object, expected: frozenset[str], *, subject: str
) -> tuple[Mapping[str, object] | None, list[Finding]]:
    if not isinstance(value, Mapping):
        return None, [_finding("metadata.schema", subject)]
    if set(value) - expected:
        return None, [_finding("metadata.unknown_field", subject)]
    if set(value) != expected:
        return None, [_finding("metadata.schema", subject)]
    return cast(Mapping[str, object], value), []


def _finding(code: str, subject: str) -> Finding:
    return Finding(
        code=code,
        severity="error",
        message=_SAFE_MESSAGES[code],
        subject=subject,
        authority="metadata-schema",
        authority_version="1.0",
        remediation="Correct the named candidate role and retry.",
        details={},
    )


def _sorted_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(findings, key=canonical_json_bytes))


def _failure(
    findings: Iterable[Finding], *, category: int = 10
) -> ValidationResult[Any]:
    ordered = _sorted_findings(findings)
    primary = ordered[0]
    error = ForgeError(
        primary.code,
        category,
        _SAFE_MESSAGES[primary.code],
        findings=ordered,
        recovery_instructions=("Correct the candidate and retry.",),
    )
    return ValidationResult.failure(error, findings=error.findings)


def _caught_failure(error: ForgeError) -> ValidationResult[Any]:
    error.with_traceback(None)
    error.__cause__ = None
    error.__context__ = None
    if error.code == "runner.untrusted":
        finding = _finding("runner.untrusted", "runner:provenance")
        sanitized = ForgeError(
            "runner.untrusted",
            15,
            _SAFE_MESSAGES["runner.untrusted"],
            findings=(finding,),
            recovery_instructions=("Use a verified installer runner.",),
        )
        return ValidationResult.failure(sanitized)
    finding = _finding(
        error.code if error.code in _SAFE_MESSAGES else "metadata.schema",
        "metadata:candidate",
    )
    return _failure((finding,), category=error.exit_category)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _measure_json(
    value: object,
    *,
    limits: LimitPolicy,
    depth: int = 1,
    members: list[int] | None = None,
) -> bool:
    if members is None:
        members = [0]
    if depth > limits.value("json_depth"):
        return False
    if isinstance(value, Mapping):
        members[0] += len(value)
        if members[0] > limits.value("json_members"):
            return False
        return all(
            _measure_json(item, limits=limits, depth=depth + 1, members=members)
            for item in value.values()
        )
    if isinstance(value, list):
        return all(
            _measure_json(item, limits=limits, depth=depth + 1, members=members)
            for item in value
        )
    return True


def _decode_json(
    raw: bytes, *, limits: LimitPolicy, subject: str
) -> tuple[Mapping[str, object] | None, list[Finding]]:
    if len(raw) > limits.value("json_record_bytes"):
        return None, [_finding("metadata.too_large", subject)]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, [_finding("metadata.invalid_utf8", subject)]
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except _DuplicateKey:
        return None, [_finding("metadata.duplicate_key", subject)]
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None, [_finding("metadata.schema", subject)]
    if not _measure_json(value, limits=limits):
        return None, [_finding("metadata.too_large", subject)]
    if not isinstance(value, Mapping):
        return None, [_finding("metadata.root_type", subject)]
    return cast(Mapping[str, object], value), []


def _is_safe_reference(raw: str, limits: LimitPolicy) -> bool:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        return False
    try:
        if len(raw.encode("utf-8")) > limits.value("path_bytes"):
            return False
    except UnicodeEncodeError:
        return False
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    parts = raw.split("/")
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ":" not in raw
        and 1 <= len(parts) <= limits.value("path_components")
        and all(
            part not in {"", ".", ".."}
            and len(part.encode("utf-8")) <= limits.value("path_component_bytes")
            for part in parts
        )
    )


def _normalize_reference(
    raw: object,
    *,
    limits: LimitPolicy,
    subject: str,
    directory: bool = False,
) -> tuple[str | None, list[Finding]]:
    if not isinstance(raw, str):
        return None, [_finding("metadata.schema", subject)]
    try:
        if len(raw.encode("utf-8")) > limits.value("path_bytes"):
            return None, [_finding("metadata.reference_unsafe", subject)]
    except UnicodeEncodeError:
        return None, [_finding("metadata.reference_unsafe", subject)]
    normalized = raw[2:] if raw.startswith("./") else raw
    if directory:
        normalized = normalized.removesuffix("/")
    if not _is_safe_reference(normalized, limits) or (
        not directory and _ASSET_REFERENCE.fullmatch(normalized) is None
    ):
        return None, [_finding("metadata.reference_unsafe", subject)]
    return normalized, []


def _authority_reference(
    raw: str, *, role: str, limits: LimitPolicy
) -> SafeRelativePath:
    """Validate a reference through the one platform-neutral path grammar."""

    result = validate_reference(raw, role=role, limits=limits)
    if not result.is_ok:
        error = result.error
        if error is None:
            raise ForgeError(
                "metadata.reference_unsafe",
                10,
                _SAFE_MESSAGES["metadata.reference_unsafe"],
            )
        raise error
    return result.unwrap()


def _decode_plugin(
    document: Mapping[str, object], *, trusted: TrustedPolicySet
) -> tuple[PluginContract | None, list[Finding]]:
    subject = "metadata:plugin"
    plugin, issues = _mapping_fields(document, _PLUGIN_FIELDS, subject=subject)
    if plugin is None:
        return None, issues
    if plugin.get("name") != "zagrosi-forge":
        issues.append(_finding("metadata.schema", subject))
    version = plugin.get("version")
    if isinstance(version, str):
        if not _is_release_version(version):
            issues.append(_finding("metadata.version", subject))
    else:
        issues.append(_finding("metadata.schema", subject))
    for key, maximum in (
        ("description", 4_096),
        ("homepage", 2_048),
        ("repository", 2_048),
        ("license", 128),
    ):
        if not _string(plugin.get(key), maximum=maximum):
            issues.append(_finding("metadata.schema", subject))

    author, author_issues = _mapping_fields(
        plugin.get("author"), _AUTHOR_FIELDS, subject=subject
    )
    issues.extend(author_issues)
    if author is not None and (
        not _string(author.get("name"), maximum=128)
        or not _string(author.get("url"), maximum=2_048)
    ):
        issues.append(_finding("metadata.schema", subject))

    keywords = _string_list(plugin.get("keywords"), maximum_items=64, maximum_bytes=63)
    if keywords is None:
        issues.append(_finding("metadata.schema", subject))

    raw_skills = plugin.get("skills")
    raw_scripts = plugin.get("scripts")
    skills, skill_issues = _normalize_reference(
        raw_skills, limits=trusted.limits, subject=subject, directory=True
    )
    scripts, script_issues = _normalize_reference(
        raw_scripts, limits=trusted.limits, subject=subject, directory=True
    )
    issues.extend(skill_issues)
    issues.extend(script_issues)
    if (
        isinstance(raw_skills, str)
        and isinstance(raw_scripts, str)
        and (
            raw_skills != "./skills/"
            or raw_scripts != "./scripts/"
            or skills != "skills"
            or scripts != "scripts"
        )
    ):
        issues.append(_finding("metadata.reference_unsafe", subject))

    interface, interface_issues = _mapping_fields(
        plugin.get("interface"), _INTERFACE_FIELDS, subject=subject
    )
    issues.extend(interface_issues)
    assets: list[str] = []
    if interface is not None:
        for key, maximum in (
            ("displayName", 128),
            ("shortDescription", 512),
            ("longDescription", 4_096),
            ("developerName", 128),
            ("category", 128),
            ("websiteURL", 2_048),
            ("privacyPolicyURL", 2_048),
            ("termsOfServiceURL", 2_048),
        ):
            if not _string(interface.get(key), maximum=maximum):
                issues.append(_finding("metadata.schema", subject))
        for key, maximum_items, maximum_bytes in (
            ("capabilities", 32, 128),
            ("defaultPrompt", 32, 1_024),
        ):
            if (
                _string_list(
                    interface.get(key),
                    maximum_items=maximum_items,
                    maximum_bytes=maximum_bytes,
                )
                is None
            ):
                issues.append(_finding("metadata.schema", subject))
        color = interface.get("brandColor")
        if (
            not isinstance(color, str)
            or re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None
        ):
            issues.append(_finding("metadata.schema", subject))
        screenshots = _string_list(
            interface.get("screenshots"), maximum_items=16, maximum_bytes=240
        )
        icon, icon_issues = _normalize_reference(
            interface.get("composerIcon"), limits=trusted.limits, subject=subject
        )
        issues.extend(icon_issues)
        if icon is not None:
            assets.append(icon)
        if screenshots is None:
            issues.append(_finding("metadata.schema", subject))
        else:
            for raw in screenshots:
                reference, reference_issues = _normalize_reference(
                    raw, limits=trusted.limits, subject=subject
                )
                issues.extend(reference_issues)
                if reference is not None:
                    assets.append(reference)

    if issues:
        return None, issues
    assert isinstance(version, str)
    assert author is not None
    assert keywords is not None
    assert skills is not None and scripts is not None
    return (
        PluginContract(
            schema_version="1.0",
            compatibility_authority="codex-0.144.4-profile",
            name="zagrosi-forge",
            version=version,
            description=cast(str, plugin["description"]),
            author_name=cast(str, author["name"]),
            author_url=cast(str, author["url"]),
            homepage=cast(str, plugin["homepage"]),
            repository=cast(str, plugin["repository"]),
            license=cast(str, plugin["license"]),
            keywords=keywords,
            skills_root=skills,
            scripts_root=scripts,
            asset_references=tuple(sorted(assets)),
            source_manifest_digest=hashlib.sha256(
                canonical_json_bytes(document)
            ).hexdigest(),
        ),
        [],
    )


def _decode_marketplace(
    document: Mapping[str, object], *, trusted: TrustedPolicySet
) -> tuple[MarketplaceContract | None, list[Finding]]:
    subject = "metadata:marketplace"
    marketplace, issues = _mapping_fields(
        document, _MARKETPLACE_FIELDS, subject=subject
    )
    if marketplace is None:
        return None, issues
    if marketplace.get("name") != "zagrosi":
        issues.append(_finding("metadata.schema", subject))
    interface, interface_issues = _mapping_fields(
        marketplace.get("interface"), _MARKETPLACE_INTERFACE_FIELDS, subject=subject
    )
    issues.extend(interface_issues)
    if interface is not None and not _string(interface.get("displayName"), maximum=128):
        issues.append(_finding("metadata.schema", subject))

    raw_plugins = marketplace.get("plugins")
    if not isinstance(raw_plugins, list) or len(raw_plugins) > 64:
        issues.append(_finding("metadata.schema", subject))
        raw_plugins = []
    entries: list[MarketplaceEntry] = []
    names: list[str] = []
    for raw_entry in raw_plugins:
        entry, entry_issues = _mapping_fields(raw_entry, _ENTRY_FIELDS, subject=subject)
        issues.extend(entry_issues)
        if entry is None:
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            issues.append(_finding("metadata.schema", subject))
            continue
        source, source_issues = _mapping_fields(
            entry.get("source"), _SOURCE_FIELDS, subject=subject
        )
        policy, policy_issues = _mapping_fields(
            entry.get("policy"), _POLICY_FIELDS, subject=subject
        )
        issues.extend(source_issues)
        issues.extend(policy_issues)
        category = entry.get("category")
        if not _string(category, maximum=128):
            issues.append(_finding("metadata.schema", subject))
        if source is not None and (
            source.get("source") != "local" or source.get("path") != "./"
        ):
            issues.append(_finding("metadata.reference_unsafe", subject))
        if policy is not None and (
            policy.get("installation") != "AVAILABLE"
            or policy.get("authentication") != "ON_INSTALL"
        ):
            issues.append(_finding("metadata.schema", subject))
        names.append(name)
        if source is not None and policy is not None and isinstance(category, str):
            entries.append(
                MarketplaceEntry(
                    name=name,
                    source_kind=cast(str, source.get("source")),
                    source_path=cast(str, source.get("path")),
                    category=category,
                    installation_policy=cast(str, policy.get("installation")),
                    authentication_policy=cast(str, policy.get("authentication")),
                )
            )
    if len(set(names)) != len(names):
        issues.append(_finding("metadata.duplicate_plugin", subject))
    selected = names.count("zagrosi-forge")
    if selected > 1:
        issues.append(_finding("metadata.duplicate_plugin", subject))
    elif selected != 1 or len(names) != 1:
        issues.append(_finding("metadata.selected_plugin", subject))
    if issues:
        return None, issues
    assert interface is not None
    return (
        MarketplaceContract(
            schema_version="1.0",
            name="zagrosi",
            display_name=cast(str, interface["displayName"]),
            plugins=tuple(entries),
            selected_plugin="zagrosi-forge",
            source_manifest_digest=hashlib.sha256(
                canonical_json_bytes(document)
            ).hexdigest(),
        ),
        [],
    )


def _toml_within_limits(
    value: object,
    *,
    limits: LimitPolicy,
    depth: int = 1,
    nodes: list[int] | None = None,
) -> bool:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > limits.value("toml_depth") or nodes[0] > limits.value("toml_nodes"):
        return False
    if isinstance(value, Mapping):
        return all(
            _toml_within_limits(item, limits=limits, depth=depth + 1, nodes=nodes)
            for item in value.values()
        )
    if isinstance(value, list):
        return all(
            _toml_within_limits(item, limits=limits, depth=depth + 1, nodes=nodes)
            for item in value
        )
    return True


def _decode_project(
    raw: bytes, *, trusted: TrustedPolicySet
) -> tuple[str | None, list[Finding]]:
    subject = "metadata:project"
    if len(raw) > trusted.limits.value("toml_bytes"):
        return None, [_finding("metadata.too_large", subject)]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, [_finding("metadata.invalid_utf8", subject)]
    try:
        document = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        return None, [_finding("metadata.schema", subject)]
    if not _toml_within_limits(document, limits=trusted.limits):
        return None, [_finding("metadata.too_large", subject)]
    project = document.get("project")
    if not isinstance(project, dict) or project.get("name") != "zagrosi-forge":
        return None, [_finding("metadata.schema", subject)]
    version = project.get("version")
    if not isinstance(version, str):
        return None, [_finding("metadata.schema", subject)]
    if not _is_release_version(version):
        return None, [_finding("metadata.version", subject)]
    return version, []


def _open_regular(
    source_root: SourceRoot,
    relative: str,
    *,
    trusted: TrustedPolicySet,
    subject: str,
) -> tuple[OpenedRegularFile | None, list[Finding]]:
    try:
        reference = _authority_reference(
            relative, role=subject.replace(":", "-"), limits=trusted.limits
        )
        return source_root.open_regular_file(reference), []
    except ForgeError as error:
        error.with_traceback(None)
        if error.code in {"path.missing", "path.not_found"}:
            code = "metadata.reference_missing"
        elif error.code == "metadata.too_large":
            code = "metadata.too_large"
        elif error.code == "metadata.reference_unsafe":
            code = "metadata.reference_unsafe"
        else:
            code = "metadata.reference_type"
        return None, [_finding(code, subject)]
    except FileNotFoundError:
        return None, [_finding("metadata.reference_missing", subject)]
    except (NotADirectoryError, IsADirectoryError, OSError):
        return None, [_finding("metadata.reference_type", subject)]


def _read_opened(
    opened: OpenedRegularFile,
    *,
    trusted: TrustedPolicySet,
    subject: str,
    toml: bool = False,
) -> tuple[bytes | None, list[Finding]]:
    limit = trusted.limits.value("toml_bytes" if toml else "json_record_bytes")
    if (
        isinstance(opened.size, bool)
        or not isinstance(opened.size, int)
        or opened.size < 0
    ):
        return None, [_finding("metadata.reference_type", subject)]
    if opened.size > limit:
        return None, [_finding("metadata.too_large", subject)]
    try:
        return opened.read_bytes(limit=limit), []
    except ForgeError as error:
        error.with_traceback(None)
        code = (
            "metadata.too_large"
            if error.code in {"metadata.too_large", "path.size"}
            else "metadata.reference_type"
        )
        return None, [_finding(code, subject)]
    except OSError:
        return None, [_finding("metadata.reference_type", subject)]


def _close_capability(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _open_and_read(
    source_root: SourceRoot,
    relative: str,
    *,
    trusted: TrustedPolicySet,
    subject: str,
    toml: bool = False,
) -> tuple[bytes | None, list[Finding], OpenedRegularFile | None]:
    opened, issues = _open_regular(
        source_root, relative, trusted=trusted, subject=subject
    )
    if opened is None:
        return None, issues, None
    raw, issues = _read_opened(opened, trusted=trusted, subject=subject, toml=toml)
    if raw is None:
        _close_capability(opened)
        return None, issues, None
    return raw, issues, opened


def _snapshot_identity(
    snapshot: SourceSnapshot,
    references: tuple[str, ...],
    *,
    trusted: TrustedPolicySet,
) -> tuple[str | None, list[Finding]]:
    entries: list[dict[str, object]] = []
    total = 0
    member_limit = trusted.limits.value("bundle_member_bytes")
    total_limit = trusted.limits.value("bundle_total_bytes")
    try:
        for relative in references:
            reference = _authority_reference(
                relative, role="metadata-snapshot", limits=trusted.limits
            )
            opened = snapshot.file(reference)
            if (
                isinstance(opened.size, bool)
                or not isinstance(opened.size, int)
                or opened.size < 0
                or opened.size > member_limit
            ):
                return None, [_finding("metadata.too_large", "metadata:reference")]
            total += opened.size
            if total > total_limit:
                return None, [_finding("metadata.too_large", "metadata:reference")]
            raw = snapshot.read_bytes(reference, limit=member_limit)
            entries.append(
                {
                    "content_digest": hashlib.sha256(raw).hexdigest(),
                    "identity": opened.identity,
                    "path": relative,
                    "size": len(raw),
                }
            )
    except ForgeError as error:
        code = (
            "metadata.too_large"
            if error.code in {"metadata.too_large", "path.size"}
            else "metadata.reference_type"
        )
        return None, [_finding(code, "metadata:reference")]
    except (KeyError, OSError, TypeError, ValueError):
        return None, [_finding("metadata.reference_type", "metadata:reference")]
    projection = {
        "authority_version": trusted.authority_version,
        "files": entries,
        "root_identity": snapshot.root_identity,
    }
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest(), []


def validate_package(
    candidate_root: os.PathLike[str],
    *,
    runner: RunnerProvenance,
    trusted: TrustedPolicySet,
    path_authority: PlatformPathAuthority,
) -> ValidationResult[ValidatedPackage]:
    """Turn bounded candidate data into a handle-backed validated package."""

    try:
        require_runner_authority(runner, RunnerOperation.CLAIM_CANDIDATE_VALID)
    except ForgeError as error:
        return cast(ValidationResult[ValidatedPackage], _caught_failure(error))
    if not _is_installed_trusted_policy_set(trusted):
        return cast(
            ValidationResult[ValidatedPackage],
            _failure((_finding("metadata.policy_mismatch", "metadata:authority"),)),
        )
    if not _is_sealed_path_authority(path_authority):
        return cast(
            ValidationResult[ValidatedPackage],
            _failure(
                (_finding("metadata.reference_type", "metadata:candidate"),),
                category=11,
            ),
        )
    try:
        source_root = path_authority.open_source_root(candidate_root)
    except ForgeError:
        return cast(
            ValidationResult[ValidatedPackage],
            _failure(
                (_finding("metadata.reference_type", "metadata:candidate"),),
                category=11,
            ),
        )
    except OSError:
        return cast(
            ValidationResult[ValidatedPackage],
            _failure(
                (_finding("metadata.reference_type", "metadata:candidate"),),
                category=11,
            ),
        )

    issues: list[Finding] = []
    bootstrap_opened: list[OpenedRegularFile] = []
    plugin_raw, found, opened = _open_and_read(
        source_root,
        _PLUGIN_FILE,
        trusted=trusted,
        subject="metadata:plugin",
    )
    issues.extend(found)
    if opened is not None:
        bootstrap_opened.append(opened)
    marketplace_raw, found, opened = _open_and_read(
        source_root,
        _MARKETPLACE_FILE,
        trusted=trusted,
        subject="metadata:marketplace",
    )
    issues.extend(found)
    if opened is not None:
        bootstrap_opened.append(opened)
    project_raw, found, opened = _open_and_read(
        source_root,
        _PROJECT_FILE,
        trusted=trusted,
        subject="metadata:project",
        toml=True,
    )
    issues.extend(found)
    if opened is not None:
        bootstrap_opened.append(opened)

    plugin_document: Mapping[str, object] | None = None
    marketplace_document: Mapping[str, object] | None = None
    plugin: PluginContract | None = None
    marketplace: MarketplaceContract | None = None
    project_version: str | None = None
    if plugin_raw is not None:
        plugin_document, found = _decode_json(
            plugin_raw, limits=trusted.limits, subject="metadata:plugin"
        )
        issues.extend(found)
    if marketplace_raw is not None:
        marketplace_document, found = _decode_json(
            marketplace_raw, limits=trusted.limits, subject="metadata:marketplace"
        )
        issues.extend(found)
    if project_raw is not None:
        project_version, found = _decode_project(project_raw, trusted=trusted)
        issues.extend(found)
    if plugin_document is not None:
        plugin, found = _decode_plugin(plugin_document, trusted=trusted)
        issues.extend(found)
    if marketplace_document is not None:
        marketplace, found = _decode_marketplace(marketplace_document, trusted=trusted)
        issues.extend(found)
    if (
        plugin is not None
        and project_version is not None
        and plugin.version != project_version
    ):
        issues.append(_finding("metadata.version_mismatch", "metadata:version"))

    for relative, expected_digest in trusted.authority_file_digests.items():
        raw, found, opened = _open_and_read(
            source_root,
            relative,
            trusted=trusted,
            subject="metadata:authority",
        )
        issues.extend(found)
        if opened is not None:
            bootstrap_opened.append(opened)
        if raw is not None:
            if hashlib.sha256(raw).hexdigest() != expected_digest:
                issues.append(
                    _finding("package.runner_upgrade_required", "metadata:authority")
                )

    if issues:
        category = (
            15
            if all(item.code == "runner.untrusted" for item in issues)
            else 11
            if any(
                item.code in {"metadata.reference_missing", "metadata.reference_type"}
                for item in issues
            )
            else 10
        )
        for opened in bootstrap_opened:
            _close_capability(opened)
        _close_capability(source_root)
        return cast(
            ValidationResult[ValidatedPackage], _failure(issues, category=category)
        )
    assert plugin is not None
    assert marketplace is not None
    assert project_version is not None

    references = {
        _PLUGIN_FILE,
        _MARKETPLACE_FILE,
        _PROJECT_FILE,
        *trusted.authority_file_digests,
        *trusted.required_regular_references,
        *plugin.asset_references,
    }
    normalized_references = tuple(sorted(references))
    validated_references = validate_reference_set(
        normalized_references,
        role="metadata-reference",
        limits=trusted.limits,
    )
    if not validated_references.is_ok:
        for opened in bootstrap_opened:
            _close_capability(opened)
        _close_capability(source_root)
        return cast(
            ValidationResult[ValidatedPackage],
            _failure((_finding("metadata.reference_unsafe", "metadata:reference"),)),
        )
    authority_references = validated_references.unwrap()
    try:
        snapshot = source_root.open_snapshot(
            authority_references, already_opened=bootstrap_opened
        )
    except ForgeError as error:
        error.with_traceback(None)
        code = (
            "metadata.reference_missing"
            if error.code in {"path.missing", "path.not_found"}
            else "metadata.reference_type"
        )
        _close_capability(source_root)
        for opened in bootstrap_opened:
            _close_capability(opened)
        return cast(
            ValidationResult[ValidatedPackage],
            _failure((_finding(code, "metadata:reference"),), category=11),
        )
    except FileNotFoundError:
        _close_capability(source_root)
        for opened in bootstrap_opened:
            _close_capability(opened)
        return cast(
            ValidationResult[ValidatedPackage],
            _failure(
                (_finding("metadata.reference_missing", "metadata:reference"),),
                category=11,
            ),
        )
    except (NotADirectoryError, IsADirectoryError, OSError):
        _close_capability(source_root)
        for opened in bootstrap_opened:
            _close_capability(opened)
        return cast(
            ValidationResult[ValidatedPackage],
            _failure(
                (_finding("metadata.reference_type", "metadata:reference"),),
                category=11,
            ),
        )

    for opened in bootstrap_opened:
        _close_capability(opened)
    snapshot_identity, identity_issues = _snapshot_identity(
        snapshot, normalized_references, trusted=trusted
    )
    if snapshot_identity is None:
        snapshot.close()
        _close_capability(source_root)
        return cast(
            ValidationResult[ValidatedPackage],
            _failure(
                identity_issues,
                category=(
                    11
                    if any(
                        item.code == "metadata.reference_type"
                        for item in identity_issues
                    )
                    else 10
                ),
            ),
        )
    value = ValidatedPackage(
        plugin=plugin,
        marketplace=marketplace,
        base_release_version=project_version,
        references=authority_references,
        trusted_policy_digests=trusted.authority_file_digests,
        source_snapshot_identity=snapshot_identity,
        findings=(),
        source_snapshot=snapshot,
        _token=_VALIDATED_PACKAGE_TOKEN,
    )
    _close_capability(source_root)
    return ValidationResult(value=value, error=None, findings=())


def inspect_package_untrusted(
    candidate_root: os.PathLike[str],
    *,
    runner: RunnerProvenance,
    trusted: TrustedPolicySet,
    path_authority: PlatformPathAuthority,
) -> DiagnosticReport:
    """Return a non-authoritative report without minting source capabilities."""

    try:
        require_runner_authority(runner, RunnerOperation.DIAGNOSTIC)
    except ForgeError:
        return DiagnosticReport(
            findings=(_finding("runner.untrusted", "runner:provenance"),)
        )
    if not _is_installed_trusted_policy_set(trusted):
        return DiagnosticReport(
            findings=(_finding("metadata.policy_mismatch", "metadata:authority"),)
        )
    if not _is_sealed_path_authority(path_authority):
        return DiagnosticReport(
            findings=(_finding("metadata.reference_type", "metadata:candidate"),)
        )
    try:
        source_root = path_authority.open_source_root(candidate_root)
    except (ForgeError, OSError):
        return DiagnosticReport(
            findings=(_finding("metadata.reference_type", "metadata:candidate"),)
        )

    issues: list[Finding] = []
    decoded_plugin: PluginContract | None = None
    decoded_marketplace: MarketplaceContract | None = None
    project_version: str | None = None
    try:
        for relative, subject, is_toml in (
            (_PLUGIN_FILE, "metadata:plugin", False),
            (_MARKETPLACE_FILE, "metadata:marketplace", False),
            (_PROJECT_FILE, "metadata:project", True),
        ):
            raw, found, opened = _open_and_read(
                source_root,
                relative,
                trusted=trusted,
                subject=subject,
                toml=is_toml,
            )
            issues.extend(found)
            if opened is not None:
                _close_capability(opened)
            if raw is None:
                continue
            if relative == _PROJECT_FILE:
                project_version, found = _decode_project(raw, trusted=trusted)
                issues.extend(found)
                continue
            document, found = _decode_json(raw, limits=trusted.limits, subject=subject)
            issues.extend(found)
            if document is None:
                continue
            if relative == _PLUGIN_FILE:
                decoded_plugin, found = _decode_plugin(document, trusted=trusted)
            else:
                decoded_marketplace, found = _decode_marketplace(
                    document, trusted=trusted
                )
            issues.extend(found)

        if (
            decoded_plugin is not None
            and project_version is not None
            and decoded_plugin.version != project_version
        ):
            issues.append(_finding("metadata.version_mismatch", "metadata:version"))
        for relative, expected_digest in trusted.authority_file_digests.items():
            raw, found, opened = _open_and_read(
                source_root,
                relative,
                trusted=trusted,
                subject="metadata:authority",
            )
            issues.extend(found)
            if opened is not None:
                _close_capability(opened)
            if raw is not None and hashlib.sha256(raw).hexdigest() != expected_digest:
                issues.append(
                    _finding("package.runner_upgrade_required", "metadata:authority")
                )
    finally:
        _close_capability(source_root)
    del decoded_marketplace
    return DiagnosticReport(findings=_sorted_findings(issues))
