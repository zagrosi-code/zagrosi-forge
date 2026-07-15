"""Static and digest-derived installer version contracts."""

from importlib.metadata import version
import re

from .contracts import ForgeError


VERSION = version("zagrosi-forge")


def base_version() -> str:
    """Return the static release version from installed package metadata."""

    return VERSION


def derive_install_version(base_version: str, base_payload_digest: str) -> str:
    """Derive the destination-independent local install version."""

    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", base_version):
        raise ForgeError(
            "diagnostic.value_rejected", 10, "Base version is not a release SemVer."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", base_payload_digest):
        raise ForgeError(
            "diagnostic.value_rejected", 10, "Payload digest is not lowercase SHA-256."
        )
    return f"{base_version}+codex.local-{base_payload_digest[:32]}"


def require_digest_match(expected: str, actual: str) -> None:
    """Stop on the impossible same-path/different-full-digest relation."""

    if not re.fullmatch(r"[0-9a-f]{64}", expected) or not re.fullmatch(
        r"[0-9a-f]{64}", actual
    ):
        raise ForgeError("diagnostic.value_rejected", 10, "Payload digest is invalid.")
    if expected != actual:
        raise ForgeError(
            "identity.digest_collision",
            12,
            "Install identity path is occupied by different content.",
        )
