"""Capture attributed release artifact identities and verify a receiver's copy offline."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from . import delivery, group_evidence, mutable_inputs, output, team_git, team_plans, team_state
from .delivery_cli import ARTIFACT_STAGES


SCHEMA = "forge-delivery-artifact-v1"
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def _file_identity(path: Path) -> dict:
    """Hash a regular file in bounded memory and detect replacement or edits during the read."""
    source = path.stat()
    if not stat.S_ISREG(source.st_mode):
        raise ValueError("The artifact must be a regular file.")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("The artifact must be a regular file.")
        if not os.path.samestat(source, before):
            raise ValueError("The artifact changed while opening it. Retry with a completed file.")
        checksum = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
            size += len(chunk)
        current = path.stat()
        # Windows path stat and fstat can use different clocks for ctime.
        after = os.fstat(stream.fileno())
        if (not os.path.samestat(before, current) or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
            raise ValueError("The artifact changed while it was being hashed. Retry with a completed file.")
    return {"kind": "file", "filename": path.name, "size_bytes": size, "digest": "sha256:" + checksum.hexdigest()}


def _image_identity(reference: str) -> dict:
    if not team_state.is_plain_text(reference, 1024):
        raise ValueError("Invalid image reference.")
    repository, separator, digest = reference.rpartition("@")
    if not SHA256.fullmatch(digest):
        raise ValueError("Supply an immutable image digest (sha256: followed by 64 lowercase hex digits).")
    if separator and (not re.fullmatch(r"[a-zA-Z0-9\[\]][a-zA-Z0-9.\[\]:_/-]*", repository)
                      or any(part in {"", ".", ".."} for part in repository.split("/"))):
        raise ValueError("Use a repository-qualified image reference such as registry.example/app@sha256:….")
    return {"kind": "image", "image": reference, "digest": digest}


def _validate(receipt: dict) -> None:
    """The receipt travels independently of the Git ledger. Validate its shape before comparing artifacts."""
    common = {"schema", "pr", "sha", "stage", "environment", "name", "source", "actor", "observed_at",
              "kind", "digest"}
    kind = receipt.get("kind")
    if kind not in ("file", "image"):
        raise ValueError("Unknown artifact kind. Expected a file or an image.")
    subject_fields = {"size_bytes", "filename"} if kind == "file" else {"image"}
    if receipt.keys() != common | subject_fields or receipt["schema"] != SCHEMA:
        raise ValueError("Invalid release artifact receipt schema.")
    for field, limit in (("pr", 200), ("name", 256), ("environment", 256), ("source", 4096), ("actor", 512)):
        if not team_state.is_plain_text(receipt[field], limit):
            raise ValueError(f"Supply a nonempty, single-line artifact {field} within {limit} characters.")
    if not COMMIT.fullmatch(receipt["sha"]) or receipt["stage"] not in ARTIFACT_STAGES:
        raise ValueError("Invalid artifact commit or release stage.")
    if group_evidence.utc_time(receipt["observed_at"]) != receipt["observed_at"]:
        raise ValueError("Invalid artifact observation time.")
    if not SHA256.fullmatch(receipt["digest"]):
        raise ValueError("Invalid artifact SHA-256 digest.")
    if kind == "file":
        if (not team_state.is_plain_text(receipt["filename"], 1024) or type(receipt["size_bytes"]) is not int
                or receipt["size_bytes"] < 0):
            raise ValueError("Invalid artifact filename or byte count.")
    elif _image_identity(receipt["image"])["digest"] != receipt["digest"]:
        raise ValueError("Image reference and recorded digest disagree.")


def _record(args, subject: dict) -> dict:
    repo = delivery.Repository(args.target_dir)
    sha = repo.commit(args.sha)
    provenance = delivery._provenance(args)
    fields = {"schema": SCHEMA, "pr": args.pr, "sha": sha, "stage": args.stage, "environment": args.environment.strip(),
              "name": args.name.strip(), **subject, **provenance}
    _validate(fields)
    with delivery.edit_ledger(repo.ledger) as prs:
        if args.pr not in prs:
            raise ValueError(f"Unknown PR: {args.pr}.")
        row = prs[args.pr]
        identity = delivery._identity(repo, row)
        if sha != (identity["merged_sha"] or identity["head_sha"]):
            raise ValueError("Record the artifact against the current PR head or its exact landing commit.")
        artifacts = row.setdefault("artifacts", [])
        # A retry with an implicit timestamp preserves the clock of its first capture.
        replay_fields = fields.copy()
        if not args.observed_at:
            del replay_fields["observed_at"]
        for previous in artifacts:
            if all(previous.get(key) == value for key, value in replay_fields.items()):
                return {"success": True, "artifact": previous}
        receipt = {**fields, "receipt_id": "sha256:" + mutable_inputs.digest(fields)}
        artifacts.append(receipt)
    return {"success": True, "artifact": receipt}


def _read_receipt(path: Path) -> dict:
    # The same bounded unambiguous JSON reader accepts the other portable delivery receipts.
    report = team_git.decode_json(team_plans._read(path, 128 * 1024))
    if not isinstance(report, dict) or report.get("success") is not True or not isinstance(report.get("artifact"), dict):
        raise ValueError("Supply a successful delivery artifact record as the receipt.")
    receipt = report["artifact"]
    fields = dict(receipt)
    receipt_id = fields.pop("receipt_id", None)
    _validate(fields)
    if receipt_id != "sha256:" + mutable_inputs.digest(fields):
        raise ValueError("The receipt ID differs from its contents.")
    return receipt


def _verify(receipt: dict, subject: dict) -> dict:
    if subject["kind"] != receipt["kind"]:
        raise ValueError(f'The receipt requires an artifact of kind {receipt["kind"]}.')
    matched = subject["digest"] == receipt["digest"]
    if subject["kind"] == "file":
        matched = matched and subject["size_bytes"] == receipt["size_bytes"]
        verification = "file_bytes"
    else:
        # A qualified receipt identifies the repository as well as the manifest digest.
        matched = matched and ("@" not in receipt["image"] or subject["image"] == receipt["image"])
        verification = "reported_reference"
    return {"success": matched, "matched": matched, "verification": verification, "artifact": receipt, "actual": subject}


def run(args):
    try:
        receipt = _read_receipt(Path(args.receipt)) if args.artifact_action == "verify" else None
        subject = _file_identity(Path(args.file)) if args.file is not None else _image_identity(args.image)
        result = _verify(receipt, subject) if receipt is not None else _record(args, subject)
    except (OSError, TypeError, ValueError) as exc:
        result = {"success": False, "error": str(exc)}
    return output.print_json(result, 0 if result["success"] else 1)
