"""Opt-in native argument checks; no packets, authentication or model requests."""
from __future__ import annotations

from pathlib import Path
import re

PROBE_TIMEOUT = 10
PROBE_OUTPUT_LIMIT = 64 * 1024


def _complete(result: dict) -> bool:
    return not any(result.get(key) for key in ("timed_out", "termination_error", "stdout_truncated", "stderr_truncated"))


def _rejected_flags(result: dict, argv: list[str]) -> list[str]:
    text = result["stdout"] + "\n" + result.get("stderr", "")
    rejected = re.findall(r"(?:unknown|unrecognized|unexpected|unsupported) (?:arguments?|options?|flags?):?\s+['\"]?(?:--)?([\w-]+)", text, re.I)
    return sorted({"--" + flag for flag in rejected if "--" + flag in argv})


def check_cli(argv: list[str], workspace: Path, execute) -> dict:
    """Check the actual review arguments with --help; help may hide valid flags."""
    report = {"status": "unknown", "version": None}
    bounds = {"timeout": PROBE_TIMEOUT, "output_limit": PROBE_OUTPUT_LIMIT}
    version = execute([argv[0], "--version"], workspace, **bounds)
    if not _complete(version):
        report["reason"] = "The CLI version probe did not complete within its bounds."
        return report
    if version["returncode"] == 0:
        match = re.fullmatch(r"(?:codex-cli\s+)?(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)(?:\s+\(Claude Code\))?", version["stdout"].strip())
        if match:
            report["version"] = match[1][:64]
    help_result = execute([*argv, "--help"], workspace, **bounds)
    if not _complete(help_result):
        report["reason"] = "The CLI help probe did not complete within its bounds."
        return report
    help_text = help_result["stdout"] + "\n" + help_result.get("stderr", "")
    if help_result["returncode"] == 0 and re.search(r"(?im)^\s*(?:usage|options|flags):", help_text):
        unsupported = "--forge-unsupported-option-check"
        control = execute([*argv, unsupported, "--help"], workspace, **bounds)
        if _complete(control) and control["returncode"] and unsupported in _rejected_flags(control, [unsupported]):
            report.update(status="compatible", reason="The CLI accepted review arguments and rejected an invalid control argument with --help; model access is unchecked.")
        else:
            report["reason"] = "The CLI help probe did not demonstrate argument validation; compatibility remains unknown."
    elif help_result["returncode"]:
        flags = _rejected_flags(help_result, argv)
        if flags:
            report.update(status="incompatible", unsupported_flags=flags,
                          reason="The CLI rejected required review arguments; select a compatible native CLI version.")
        else:
            report["reason"] = "The CLI help probe failed without a confirmed unsupported review argument."
    else:
        report["reason"] = "The CLI returned no recognizable help; compatibility remains unknown."
    return report
