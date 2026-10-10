"""Source-bound, repeatable caller checks for explicitly declared mutable sections."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

from . import markdown, mutable_inputs, policy, storage


def _json_object(pairs) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Compatibility JSON repeats {key!r}.")
        value[key] = item
    return value


def parse_declaration(text: str) -> dict | None:
    bodies = [body for title, body in markdown.markdown_h2_sections(markdown.visible_markdown(text))
              if title.casefold() == "compatibility"]
    if not bodies:
        return None
    blocks, _ = markdown.split_markdown_fences_with_closure(bodies[0])
    # Older plans used this heading for prose, without opting into captured checks.
    json_lines = [line.lstrip() for line in bodies[0].splitlines() if line.lstrip().startswith(("{", "["))]
    markdown_link = re.compile(r"\[[^\]\n]*\](?:\([^\n]*\)|\[[^\]\n]*\]|:)")
    if len(bodies) == 1 and not blocks and all(markdown_link.match(line) for line in json_lines):
        return None
    if len(bodies) != 1 or len(blocks) != 1 or blocks[0][0] != "json" or not blocks[0][2]:
        raise ValueError("Compatibility requires one heading with one closed JSON block.")
    try:
        value = json.loads("\n".join(blocks[0][1]), object_pairs_hook=_json_object)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Compatibility JSON: {exc}") from exc
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Compatibility requires an object with version 1.")
    if value.get("mode") == "not_required":
        if set(value) != {"version", "mode", "reason"} or not _substantive(value.get("reason")):
            raise ValueError("Compatibility not_required needs a substantive reason.")
        return value
    if value.get("mode") != "required" or set(value) != {
        "version", "mode", "source_paths", "check_paths", "check_provenance",
    }:
        raise ValueError("Required Compatibility needs source_paths, check_paths, and check_provenance.")
    provenance = value["check_provenance"]
    if (not isinstance(provenance, dict) or set(provenance) != {"source", "author"}
            or not isinstance(provenance["source"], str)
            or provenance["source"] not in {"existing", "independent", "writer", "external"}
            or not _substantive(provenance["author"])):
        raise ValueError("Compatibility check_provenance needs a derivation source and author attestation.")
    for field in ("source_paths", "check_paths"):
        paths = value[field]
        if not isinstance(paths, list) or not paths or not all(isinstance(name, str) and name.strip() for name in paths):
            raise ValueError(f"Compatibility {field} needs nonempty target-relative paths.")
        normalized = [Path(name).as_posix() for name in paths]
        if len(set(normalized)) != len(paths) or any(Path(name).anchor or ".." in Path(name).parts or "\0" in name for name in paths):
            raise ValueError(f"Compatibility {field} must use distinct paths within the target directory.")
        value[field] = normalized
    if any(Path(source).is_relative_to(Path(check)) or Path(check).is_relative_to(Path(source))
           for source in value["source_paths"] for check in value["check_paths"]):
        raise ValueError("Compatibility source and check paths must not overlap.")
    return value


def _substantive(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in {"", "none", "n/a", "tbd", "todo", "pending"}


def declaration(planning: Path, section: str) -> dict | None:
    if not isinstance(section, str) or not policy.SECTION_RE.fullmatch(section):
        raise ValueError("Compatibility requires a valid section name.")
    return parse_declaration(storage.read_text(planning / "sections" / f"{section}.md"))


def receipt_path(planning: Path, section: str) -> Path:
    if not isinstance(section, str) or not policy.SECTION_RE.fullmatch(section):
        raise ValueError("Compatibility requires a valid section name.")
    return planning / "implementation" / "verification" / f"{section}-compatibility.json"


def _identity(planning: Path, target: Path, section: str, declared: dict) -> dict:
    return {"version": 1, "planning_dir": str(planning.resolve()), "target_dir": str(target.resolve()),
            "section": section, "declaration": declared,
            "contract_digest": mutable_inputs.digest(mutable_inputs.contract_inputs(planning, section)[0])}


def _observations(target: Path, declared: dict, *, allow_missing_checks: bool = False) -> tuple[dict, dict]:
    source, source_inodes = mutable_inputs.regular_tree_observations(target, declared["source_paths"])
    checks, check_inodes = mutable_inputs.regular_tree_observations(target, declared["check_paths"], allow_missing=allow_missing_checks)
    if source_inodes & check_inodes:
        raise ValueError("Compatibility source and check paths overlap through file aliases.")
    if not source_inodes or (not allow_missing_checks and not check_inodes):
        raise ValueError("Compatibility source and check inputs must contain regular files.")
    return source, checks


def _hashes(value, paths) -> bool:
    return (isinstance(value, dict) and set(value) == set(paths)
            and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in value.values()))


def _validate_pair(pair, identity: dict) -> None:
    if (not isinstance(pair, dict) or type(pair.get("version")) is not int
            or any(pair.get(key) != value for key, value in identity.items())):
        raise ValueError("Compatibility origin has a changed contract, target, or malformed identity; it cannot be reset automatically.")
    origin = pair.get("origin")
    if (not isinstance(origin, dict) or not _substantive(origin.get("captured_at"))
            or not _hashes(origin.get("source"), identity["declaration"]["source_paths"])):
        raise ValueError("Compatibility origin is malformed; it cannot be reset automatically.")
    history = pair.get("prior_attempts")
    if not isinstance(history, list) or not all(isinstance(item, dict) and isinstance(item.get("stage"), str)
                                             and item["stage"] in {"baseline", "candidate"}
                                             and isinstance(item.get("result"), dict) for item in history):
        raise ValueError("Compatibility prior_attempts is malformed.")
    for stage in ("baseline", "candidate"):
        if stage in pair and not isinstance(pair[stage], dict):
            raise ValueError(f"Compatibility {stage} is malformed.")


def activate(planning: Path, target: Path, section: str) -> dict | None:
    """Anchor the selected section once. The setup caller holds the mutable lock."""
    mutable_inputs.prepared_contract(planning, target)
    declared = declaration(planning, section)
    if not declared or declared["mode"] == "not_required":
        return declared
    identity = _identity(planning, target, section, declared)
    path = receipt_path(planning, section)
    if path.exists():
        pair = json.loads(storage.read_text(path), object_pairs_hook=_json_object)
        _validate_pair(pair, identity)
        return pair
    source, _ = _observations(target, declared, allow_missing_checks=True)
    pair = {**identity, "origin": {"source": source, "captured_at": storage.now_iso()}, "prior_attempts": []}
    mutable_inputs.prepared_contract(planning, target)
    storage.write_json(path, pair)
    return pair


def _load_pair(planning: Path, target: Path, section: str) -> dict:
    declared = declaration(planning, section)
    if not declared or declared["mode"] != "required":
        raise ValueError("This section has no required Compatibility declaration.")
    path = receipt_path(planning, section)
    if not path.is_file():
        raise ValueError("Compatibility origin is missing; activate this section before changing source.")
    pair = json.loads(storage.read_text(path), object_pairs_hook=_json_object)
    _validate_pair(pair, _identity(planning, target, section, declared))
    return pair


def _origin_digest(pair: dict) -> str:
    return mutable_inputs.digest({key: value for key, value in pair.items()
                                 if key not in {"baseline", "candidate", "prior_attempts"}})


def _stage_error(pair: dict, stage: str) -> str | None:
    from .verification import result_error

    result = pair.get(stage)
    if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
        return f"Compatibility {stage} is missing or malformed; captured execution is required."
    if error := result_error(result):
        return f"Compatibility {stage}: {error}"
    if result.get("source") != "captured":
        return f"Compatibility {stage} requires captured execution; attestation cannot replace it."
    snapshot = result.get("snapshot")
    if (not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or any(snapshot.get(key) != pair[key] for key in ("planning_dir", "target_dir", "section"))
            or not all(isinstance(snapshot.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", snapshot[key])
                       for key in ("source_digest", "contract_digest"))
            or type(snapshot.get("source_file_count")) is not int or snapshot["source_file_count"] < 0
            or not _hashes(result.get("protected_source"), pair["declaration"]["source_paths"])
            or not _hashes(result.get("checks"), pair["declaration"]["check_paths"])
            or result.get("origin_digest") != _origin_digest(pair)):
        return f"Compatibility {stage} has malformed or unbound inputs."
    if stage == "baseline" and result["protected_source"] != pair["origin"]["source"]:
        return "Compatibility baseline does not match the original source."
    if stage == "candidate" and (
        result.get("baseline_digest") != mutable_inputs.digest(pair["baseline"])
        or result["command"] != pair["baseline"]["command"] or result["checks"] != pair["baseline"]["checks"]
    ):
        return "Compatibility candidate does not match the captured baseline command and checks."
    return None


def capture(planning: Path, target: Path, section: str, stage: str, command: list[str], timeout: float) -> dict:
    """Run only explicit argv, publishing pending first and retaining prior attempts."""
    from .verification import _capture, result_error

    if stage not in {"baseline", "candidate"} or not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("Compatibility capture needs baseline|candidate and an explicit command array.")
    if not 0 < timeout <= 86400:
        raise ValueError("Compatibility timeout must be positive and at most 86400 seconds.")
    mutable_inputs.prepared_contract(planning, target)
    with storage.file_lock(planning / "implementation" / ".mutable-state"):
        pair = _load_pair(planning, target, section)
        source, checks = _observations(target, pair["declaration"])
        if stage == "baseline":
            if source != pair["origin"]["source"]:
                raise ValueError("Compatibility baseline requires unchanged original source; restore it explicitly before retrying.")
        else:
            if error := _stage_error(pair, "baseline"):
                raise ValueError(error)
            if checks != pair["baseline"]["checks"] or command != pair["baseline"]["command"]:
                raise ValueError("Compatibility candidate must use unchanged baseline checks and command.")
        snapshot = mutable_inputs.verification_snapshot(planning, target, section)
        pending = {"version": 1, "source": "captured", "outcome": "pending", "command": command,
                   "snapshot": snapshot, "protected_source": source, "checks": checks,
                   "origin_digest": _origin_digest(pair)}
        if stage == "candidate":
            pending["baseline_digest"] = mutable_inputs.digest(pair["baseline"])
        for replaced in (("baseline", "candidate") if stage == "baseline" else ("candidate",)):
            if replaced in pair:
                pair["prior_attempts"].append({"stage": replaced, "result": pair.pop(replaced)})
        pair[stage] = pending
        path = receipt_path(planning, section)
        storage.write_json(path, pair)
        result = pending
        try:
            mutable_inputs.prepared_contract(planning, target)
            result = {**pending, **_capture(command, target, timeout), "completed_at": storage.now_iso()}
            error = result_error(result)
            if (snapshot != mutable_inputs.verification_snapshot(planning, target, section)
                    or (source, checks) != _observations(target, pair["declaration"])):
                error = "Compatibility inputs changed during execution; the result cannot establish preservation."
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result, error = {**result, "outcome": "failed", "completed_at": storage.now_iso()}, str(exc)
        if error:
            result.update(error=error)
            if result["outcome"] in {"passed", "pending"}:
                result["outcome"] = "failed"
        pair[stage] = result
        storage.write_json(path, pair)
        return {"success": error is None, "receipt_path": str(path), "stage": stage, "error": error,
                "outcome": result["outcome"],
                **{key: result[key] for key in ("seconds", "exit_code", "stdout_tail", "stderr_tail") if key in result}}


def evidence_error(planning: Path, target: Path, section: str, evidence) -> str | None:
    """Validate a copied historical pair; later sections may legitimately change code."""
    try:
        declared = declaration(planning, section)
        if not declared:
            return None if evidence is None else "Legacy sections cannot claim captured compatibility without a declaration."
        if declared["mode"] == "not_required":
            return None if evidence == declared else "Compatibility not_required decision is missing or changed."
        _validate_pair(evidence, _identity(planning, target, section, declared))
        return _stage_error(evidence, "baseline") or _stage_error(evidence, "candidate")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return str(exc)


def completion_evidence(planning: Path, target: Path, section: str) -> dict | None:
    declared = declaration(planning, section)
    if not declared or declared["mode"] == "not_required":
        return declared
    pair = _load_pair(planning, target, section)
    if error := evidence_error(planning, target, section, pair):
        raise ValueError(error)
    source, checks = _observations(target, declared)
    result = pair["candidate"]
    if (result["snapshot"] != mutable_inputs.verification_snapshot(planning, target, section)
            or source != result["protected_source"] or checks != result["checks"]):
        raise ValueError("Compatibility candidate inputs changed; rerun unchanged baseline checks against the current candidate.")
    return pair


def status(planning: Path, target: Path, section: str) -> dict:
    """Describe the next explicit action without creating or refreshing an origin."""
    try:
        declared = declaration(planning, section)
        if not declared or declared["mode"] == "not_required":
            return {"mode": declared["mode"] if declared else "legacy", "stage": None, "error": None}
        if not receipt_path(planning, section).exists():
            _observations(target, declared, allow_missing_checks=True)
            return {"mode": "required", "stage": "activate", "error": None}
        pair = _load_pair(planning, target, section)
        source, checks = _observations(target, declared, allow_missing_checks=True)
        error = _stage_error(pair, "baseline")
        if not error and checks != pair["baseline"]["checks"]:
            error = "Compatibility checks changed after baseline capture."
        if error:
            original = source == pair["origin"]["source"]
            return {"mode": "required", "stage": "baseline" if original else "blocked",
                    "error": error if original else error + " Original source must be restored explicitly before a new baseline."}
        try:
            completion_evidence(planning, target, section)
        except ValueError as exc:
            return {"mode": "required", "stage": "candidate", "error": str(exc), "command": pair["baseline"]["command"]}
        return {"mode": "required", "stage": "complete", "error": None, "command": pair["baseline"]["command"]}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return {"mode": "required", "stage": "blocked", "error": str(exc)}
