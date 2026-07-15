"""Deterministic, bounded, role-relative diagnostic rendering."""

from __future__ import annotations

import re
import unicodedata

from .contracts import Finding, ForgeError


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_SECRET = re.compile(
    r"""(?ix)
    (?P<prefix>
        ["']?(?:token|password|secret|api[_-]?key)["']?
        \s*(?:=|:|\s)\s*
    )
    (?P<value>"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]]+)
    """
)
_BEARER = re.compile(r"""(?ix)\bbearer\s+(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]]+)""")
_UNC_PATH = re.compile(r"\\\\[^\s\\]+\\[^\s\\]+(?:\\[^\s]+)*")
_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9])[a-z]:[\\/][^\s,;\"']+")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9_.:/-])/(?!/)[^\s,;\"']+")
_DETAIL_KEYS = frozenset(
    {"actual", "correlation_id", "count", "expected", "state", "tool", "version"}
)
_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,63}(?::[a-z0-9][a-z0-9_.-]{0,63})*\Z")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}\Z")
_DETAIL_TOKEN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}\Z")
_MAX_DIAGNOSTIC_INPUT_BYTES = 4_096
_MAX_DIAGNOSTIC_OUTPUT_BYTES = 1_024

_INSTALLER_FINDING_TEXT = {
    "metadata.too_large": "Package metadata exceeds the trusted limit.",
    "metadata.invalid_utf8": "Package metadata is not valid UTF-8.",
    "metadata.duplicate_key": "Package metadata contains a duplicate key.",
    "metadata.root_type": "Package metadata has the wrong root type.",
    "metadata.schema": "Package metadata does not match the trusted schema.",
    "metadata.unknown_field": "Package metadata contains an unknown field.",
    "metadata.version": "Package metadata has an invalid release version.",
    "metadata.version_mismatch": "Package metadata release versions disagree.",
    "metadata.duplicate_plugin": "Marketplace metadata repeats a plugin entry.",
    "metadata.selected_plugin": "Marketplace metadata does not select one plugin.",
    "metadata.reference_unsafe": "Package metadata contains an unsafe reference.",
    "metadata.reference_missing": "A required package reference is missing.",
    "metadata.reference_type": "A required package reference has the wrong type.",
    "metadata.policy_mismatch": "Candidate policy data differs from trusted policy.",
    "package.runner_upgrade_required": "The runner cannot validate this package policy.",
    "runner.untrusted": "The current runner is not trusted for this operation.",
    "path.component_invalid": "A path component is invalid.",
    "path.absolute": "An absolute path is not allowed.",
    "path.traversal": "Path traversal is not allowed.",
    "path.windows_prefix": "A Windows path prefix is not allowed.",
    "path.reserved": "A reserved path component is not allowed.",
    "path.normalization_collision": "Package references collide after normalization.",
    "path.linked_ancestor": "A path ancestor is linked.",
    "path.linked_leaf": "A path leaf is linked.",
    "path.reparse_point": "A Windows reparse point is not allowed.",
    "path.hardlink": "A hard-linked file is not allowed.",
    "path.outside_root": "A path is outside its authorized root.",
    "path.overlap": "Source and destination paths overlap.",
    "path.depth": "A path has an unexpected containment depth.",
    "path.identity_changed": "An opened path identity changed.",
    "path.unsupported_filesystem": "The filesystem is not supported safely.",
    "path.root_unowned": "The existing Forge root is not owned by this installer.",
    "ownership.receipt_invalid": "The ownership receipt is invalid.",
    "ownership.receipt_corrupt": "The ownership receipt is corrupt.",
    "ownership.receipt_unsupported": "The ownership receipt requires a newer reader.",
    "ownership.receipt_conflict": "A different ownership receipt already exists.",
    "ownership.identity_mismatch": "Current state does not match the owned identity.",
    "ownership.manifest_mismatch": "Current state does not match the owned manifest.",
    "ownership.unowned": "Deletion ownership could not be proven.",
    "ownership.already_quarantined": "Owned state was already quarantined.",
    "ownership.quarantine_conflict": "The quarantine destination is occupied.",
    "ownership.cleanup_incomplete": "Quarantined cleanup is incomplete.",
}
_TRUSTED_FINDING_TEMPLATES = {
    "metadata.invalid": (
        "error",
        "Package metadata is invalid.",
        "Correct the package metadata and retry.",
    ),
    **{
        code: (
            "error",
            message,
            "Review the reported package state and retry safely.",
        )
        for code, message in _INSTALLER_FINDING_TEXT.items()
    },
}
_TRUSTED_FINDING_DETAILS = {
    "metadata.invalid": frozenset({"count", "state"}),
    **{code: frozenset() for code in _INSTALLER_FINDING_TEXT},
}
_TRUSTED_METADATA_STATES = frozenset({"rejected"})


def _diagnostic_rejected(message: str) -> ForgeError:
    return ForgeError("diagnostic.value_rejected", 10, message)


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _diagnostic_rejected("Diagnostic text is not valid Unicode.") from exc


def _truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def redact_text(value: str, *, limit: int = 1024) -> str:
    """Remove common secret/path material and control characters from text."""

    if (
        not isinstance(value, str)
        or len(value) > _MAX_DIAGNOSTIC_INPUT_BYTES
        or _utf8_size(value) > _MAX_DIAGNOSTIC_INPUT_BYTES
    ):
        raise _diagnostic_rejected("Diagnostic text exceeds the trusted limit.")
    if not 1 <= limit <= _MAX_DIAGNOSTIC_OUTPUT_BYTES:
        raise _diagnostic_rejected("Diagnostic output limit is invalid.")
    sanitized = _ANSI_ESCAPE.sub("", value)
    sanitized = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in sanitized
    )
    sanitized = _BEARER.sub("Bearer <redacted>", sanitized)
    sanitized = _SECRET.sub(
        lambda match: f"{match.group('prefix')}<redacted>", sanitized
    )
    sanitized = _UNC_PATH.sub("<redacted-path>", sanitized)
    sanitized = _WINDOWS_PATH.sub("<redacted-path>", sanitized)
    sanitized = _POSIX_PATH.sub("<redacted-path>", sanitized)
    sanitized = " ".join(sanitized.split())
    return _truncate_utf8(sanitized, limit)


def _require_role(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    if (
        not isinstance(value, str)
        or not pattern.fullmatch(value)
        or _utf8_size(value) > 128
    ):
        raise _diagnostic_rejected(f"Diagnostic {field} is not role-relative.")
    return value


def _safe_detail(key: str, value: object) -> object:
    if key == "state":
        if isinstance(value, str) and value in _TRUSTED_METADATA_STATES:
            return value
        raise _diagnostic_rejected("Diagnostic state is not trusted.")
    if key == "count" and (not isinstance(value, int) or isinstance(value, bool)):
        raise _diagnostic_rejected("Diagnostic count is invalid.")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not 0 <= value <= 2**31 - 1:
            raise _diagnostic_rejected("Diagnostic detail integer is out of range.")
        return value
    if isinstance(value, str) and _DETAIL_TOKEN.fullmatch(value):
        if redact_text(value, limit=128) == value:
            return value
    raise _diagnostic_rejected("Diagnostic detail value is not allowed.")


def finding_to_dict(finding: Finding) -> dict[str, object]:
    """Render a Finding with a closed, scalar-only details map."""

    try:
        expected_severity, safe_message, safe_remediation = _TRUSTED_FINDING_TEMPLATES[
            finding.code
        ]
    except (KeyError, TypeError) as exc:
        raise _diagnostic_rejected("Diagnostic code is not trusted.") from exc
    if finding.severity != expected_severity:
        raise _diagnostic_rejected("Diagnostic severity does not match its code.")
    if any(
        not isinstance(value, str)
        or len(value) > _MAX_DIAGNOSTIC_INPUT_BYTES
        or _utf8_size(value) > _MAX_DIAGNOSTIC_INPUT_BYTES
        for value in (finding.message, finding.remediation)
    ):
        raise _diagnostic_rejected("Diagnostic source text exceeds the trusted limit.")
    allowed_details = _TRUSTED_FINDING_DETAILS[finding.code]
    if any(
        key not in _DETAIL_KEYS or key not in allowed_details for key in finding.details
    ):
        raise _diagnostic_rejected("Diagnostic detail key is not allowed.")
    details: dict[str, object] = {}
    for key, value in finding.details.items():
        details[key] = _safe_detail(key, value)
    return {
        "authority": _require_role(finding.authority, field="authority", pattern=_ROLE),
        "authority_version": _require_role(
            finding.authority_version,
            field="authority version",
            pattern=_VERSION,
        ),
        "code": finding.code,
        "details": details,
        "message": safe_message,
        "remediation": safe_remediation,
        "severity": finding.severity,
        "subject": _require_role(finding.subject, field="subject", pattern=_ROLE),
    }
