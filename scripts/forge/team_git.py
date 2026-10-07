"""Git-backed cooperative work board; application refs and files remain untouched."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .child_process import execute
from .team_state import TeamError, validate_board

TEAM_REF = "refs/heads/forge/team"
BOARD_LIMIT = 1024 * 1024
_OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_REPOSITORY_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                   "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"}


def _run(root, *args, prompt=None, check=True, timeout=30, identity=False):
    env = {key: value for key, value in os.environ.items() if key not in _REPOSITORY_ENV}
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", GIT_NO_REPLACE_OBJECTS="1")
    if identity:
        env.update(GIT_AUTHOR_NAME="Forge", GIT_AUTHOR_EMAIL="forge@example.invalid",
                   GIT_COMMITTER_NAME="Forge", GIT_COMMITTER_EMAIL="forge@example.invalid")
    result = execute(["git", *args], Path(root), prompt=prompt, timeout=timeout,
                     env=env, inherit_env=False, output_limit=BOARD_LIMIT + 4096)
    if check and (result["returncode"] or result.get("stdout_truncated") or result.get("stderr_truncated")):
        raise TeamError("team-unavailable", "Git could not complete the team operation; check access, connectivity and repository policies.")
    return result


def _output(root, *args, **kwargs):
    return _run(root, *args, **kwargs)["stdout"].strip()


def _oid(value):
    if not isinstance(value, str) or not _OID.fullmatch(value):
        raise TeamError("team-invalid-revision", "Team revision must be a complete Git object ID.")
    return value


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def decode_json(raw):
    """Reject ambiguous JSON before validating either marker or board schema."""
    def invalid_constant(value):
        raise ValueError("non-finite value")
    try:
        return json.loads(raw, object_pairs_hook=_object, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise TeamError("team-invalid-data", "Team metadata is not valid unambiguous JSON.") from exc


@dataclass(frozen=True)
class Repository:
    root: Path
    git_dir: Path
    common_dir: Path
    branch: str | None
    head: str

    @classmethod
    def discover(cls, target):
        target = Path(target).resolve()
        try:
            root = Path(_output(target, "rev-parse", "--show-toplevel")).resolve()
            git_dir = Path(_output(root, "rev-parse", "--absolute-git-dir")).resolve()
            common_dir = Path(_output(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
            head = _oid(_output(root, "rev-parse", "--verify", "HEAD"))
            branch_result = _run(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
            if branch_result["returncode"] not in (0, 1):
                raise TeamError("team-repository", "Could not identify the current checkout branch.")
            return cls(root, git_dir, common_dir, branch_result["stdout"].strip() or None, head)
        except TeamError as exc:
            raise TeamError("team-repository", "Team collaboration requires a Git working tree with an initial commit.") from exc


@dataclass(frozen=True)
class Snapshot:
    revision: str | None
    board: dict | None


class GitBoard:
    def __init__(self, repo: Repository, remote="origin", pin=None):
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", remote):
            raise TeamError("team-remote", "Choose an existing named Git remote.")
        self.repo, self.remote, self._pin = repo, remote, pin

    def pin(self):
        fetch = _output(self.repo.root, "remote", "get-url", "--all", self.remote).splitlines()
        push = _output(self.repo.root, "remote", "get-url", "--push", "--all", self.remote).splitlines()
        if len(fetch) != 1 or len(push) != 1 or fetch != push:
            code = "team-destination-changed" if self._pin else "team-remote"
            raise TeamError(code, "Team coordination requires one matching fetch and push destination; choose a dedicated named remote and join again.")
        current = {"remote": self.remote, "endpoint_digest": self._endpoint_digest(fetch[0])}
        if self._pin and any(self._pin.get(key) != value for key, value in current.items()):
            raise TeamError("team-destination-changed", "The joined Git destination changed; inspect it and explicitly join again.")
        self._pin = current
        return current.copy()

    def _endpoint_digest(self, endpoint):
        # Relative file remotes must remain pinned to the same filesystem location.
        if ":" not in endpoint or (os.name == "nt" and len(endpoint) > 1 and endpoint[1] == ":"):
            endpoint = str((self.repo.root / endpoint).resolve())
        return hashlib.sha256(endpoint.encode()).hexdigest()

    def _git(self, command, *args, **kwargs):
        return _run(self.repo.root, "-c", f"remote.{self.remote}.mirror=false", "-c", "push.followTags=false",
                    "-c", "push.recurseSubmodules=no", command, *args, **kwargs)

    def _push(self, receipt):
        # Git push updates a named remote's tracking refs even with negative fetch
        # mappings. A command-local alias has no mappings and preserves raw URL
        # rewriting semantics; hooks still run, receiving this temporary name.
        alias = "forge-team-" + uuid.uuid4().hex
        options = ["-c", "push.followTags=false", "-c", "push.recurseSubmodules=no"]
        for key in ("url", "pushurl", "receivepack", "proxy", "proxyAuthMethod", "vcs"):
            configured = _run(self.repo.root, "config", "--null", "--get-all", f"remote.{self.remote}.{key}", check=False)
            if configured["returncode"] not in (0, 1) or configured.get("stdout_truncated"):
                raise TeamError("team-remote", "Could not inspect the joined remote's transport settings.")
            for value in configured["stdout"].split("\0")[:-1]:
                options += ["-c", f"remote.{alias}.{key}={value}"]
        # remote get-url rejects aliases that exist only in command config.
        # Transport resolves them; revalidate the source's two endpoints after
        # copying raw settings, avoiding a second rewrite of expanded URLs.
        self.pin()
        resolved = _output(self.repo.root, *options, "ls-remote", "--get-url", alias)
        if self._endpoint_digest(resolved) != self._pin["endpoint_digest"]:
            raise TeamError("team-destination-changed", "The team transport destination changed; inspect it and join again.")
        return _run(self.repo.root, *options, "push", "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
                    f"--force-with-lease={TEAM_REF}:{receipt.get('expected') or ''}", alias,
                    f"{receipt['revision']}:{TEAM_REF}", check=False)

    def read(self, board_id=None, allow_missing=False):
        self.pin()
        listing = self._git("ls-remote", "--quiet", "--exit-code", "--refs", self.remote, TEAM_REF, check=False)
        if listing["returncode"] == 2 and not listing["stdout"]:
            if allow_missing:
                return Snapshot(None, None)
            raise TeamError("team-board-missing", "The joined team board is missing; inspect the remote before recovering.")
        if listing["returncode"] or listing.get("stdout_truncated"):
            raise TeamError("team-unavailable", "Could not read the team board; teammate visibility is unknown.")
        rows = [line.split("\t") for line in listing["stdout"].splitlines()]
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != TEAM_REF:
            raise TeamError("team-invalid-data", "The remote returned an ambiguous team reference.")
        _oid(rows[0][0])
        temporary = "refs/forge/team-fetch/" + uuid.uuid4().hex
        revision = None
        try:
            self._git("fetch", "--quiet", "--no-tags", "--no-prune", "--no-prune-tags",
                      "--no-write-fetch-head", "--no-recurse-submodules", "--no-auto-maintenance",
                      "--no-write-commit-graph", "--refmap=", self.remote, f"{TEAM_REF}:{temporary}")
            revision = _oid(_output(self.repo.root, "rev-parse", "--verify", temporary))
            board = self._board_at(revision)
            if board_id is not None and board["board_id"] != board_id:
                raise TeamError("team-board-replaced", "The team board identity changed; inspect it and join again.")
            return Snapshot(revision, board)
        finally:
            if revision:
                _run(self.repo.root, "update-ref", "-d", temporary, revision, check=False)
            else:
                # A failed fetch can still have installed this invocation's private ref.
                found = _run(self.repo.root, "rev-parse", "--verify", temporary, check=False)
                if found["returncode"] == 0 and _OID.fullmatch(found["stdout"].strip()):
                    _run(self.repo.root, "update-ref", "-d", temporary, found["stdout"].strip(), check=False)

    def _board_at(self, revision):
        tree = _output(self.repo.root, "ls-tree", _oid(revision))
        match = re.fullmatch(r"100644 blob ([a-f0-9]{40}|[a-f0-9]{64})\tboard\.json", tree)
        if not match:
            raise TeamError("team-invalid-data", "The team commit must contain only a regular board.json file.")
        blob = match[1]
        if int(_output(self.repo.root, "cat-file", "-s", blob)) > BOARD_LIMIT:
            raise TeamError("team-invalid-data", "Team metadata exceeds the supported size limit.")
        raw = _output(self.repo.root, "cat-file", "blob", blob)
        if "\ufffd" in raw:
            raise TeamError("team-invalid-data", "Team metadata must use valid UTF-8.")
        return validate_board(decode_json(raw))

    def _ancestor(self, older, newer):
        result = _run(self.repo.root, "merge-base", "--is-ancestor", _oid(older), _oid(newer), check=False)
        if result["returncode"] not in (0, 1):
            raise TeamError("team-write-unknown", "The pending team publication cannot be reconciled from available Git history.")
        return result["returncode"] == 0

    def reconcile(self, receipt):
        """Return current state if applied, otherwise None; None is not a retry grant."""
        applied, current = self._reconcile(receipt)
        if applied:
            self.release(receipt)
        return current if applied else None

    def release(self, receipt):
        """Drop only this attempt's unchanged private ref after confirmation or discard."""
        self._validate_receipt(receipt)
        revision = receipt["revision"]
        _run(self.repo.root, "update-ref", "--no-deref", "-d",
             f"refs/forge/team-pending/{revision}", revision, check=False)

    def _reconcile(self, receipt):
        self._validate_receipt(receipt)
        attempt = _oid(receipt["revision"])
        expected = receipt.get("expected")
        if expected is not None:
            _oid(expected)
        current = self.read(allow_missing=expected is None)
        if current.revision is None:
            return False, current
        if current.board["board_id"] != receipt["board_id"]:
            if expected is None:
                return False, current
            raise TeamError("team-board-replaced", "The pending operation belongs to a different team board.")
        if self._ancestor(attempt, current.revision):
            return True, current
        if expected is not None and not self._ancestor(expected, current.revision):
            raise TeamError("team-write-unknown", "The team history changed; inspect the pending operation before retrying.")
        return False, current

    @staticmethod
    def _validate_receipt(receipt):
        if (not isinstance(receipt, dict) or not isinstance(receipt.get("board_id"), str) or
                not re.fullmatch(r"[a-f0-9]{32}", receipt["board_id"])):
            raise TeamError("team-write-unknown", "The pending team receipt is invalid; preserve it before recovery.")
        _oid(receipt.get("revision"))
        if receipt.get("expected") is not None:
            _oid(receipt["expected"])

    def retry(self, receipt):
        """Retry exactly the retained commit; never create a second operation."""
        self._validate_receipt(receipt)
        applied, current = self._reconcile(receipt)
        if applied:
            self.release(receipt)
            return current
        if current.revision != receipt.get("expected"):
            raise TeamError("team-contention", "The team board changed; the pending publication was not applied.")
        board = self._board_at(receipt["revision"])
        parents = _output(self.repo.root, "rev-list", "--parents", "-n", "1", receipt["revision"]).split()[1:]
        if board["board_id"] != receipt["board_id"] or parents != ([receipt["expected"]] if receipt.get("expected") else []):
            raise TeamError("team-write-unknown", "The retained team commit does not match its receipt; preserve it before recovery.")
        return self._publish(receipt, board)

    def write(self, board, expected, remember, message="Forge team update"):
        board = validate_board(board)
        self.pin()
        raw = json.dumps(board, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
        if len(raw.encode()) > BOARD_LIMIT:
            raise TeamError("team-invalid-data", "Team metadata exceeds the supported size limit.")
        blob = _oid(_output(self.repo.root, "hash-object", "-w", "--stdin", prompt=raw))
        tree = _oid(_output(self.repo.root, "mktree", prompt=f"100644 blob {blob}\tboard.json\n"))
        args = ["commit-tree", tree]
        if expected is not None:
            args += ["-p", _oid(expected)]
        signing = _run(self.repo.root, "config", "--bool", "--get", "commit.gpgSign", check=False)
        if signing["returncode"] == 0 and signing["stdout"].strip() == "true":
            args.append("-S")
        revision = _oid(_output(self.repo.root, *args,
                               prompt=message + "\n\nForge operation: " + uuid.uuid4().hex + "\n", identity=True))
        receipt = {"revision": revision, "expected": expected, "board_id": board["board_id"]}
        # The receipt alone does not keep its commit alive through Git cleanup.
        # Retain it even if remember raises after persisting the receipt.
        _run(self.repo.root, "update-ref", "--no-deref", f"refs/forge/team-pending/{revision}", revision, "")
        remember(receipt)
        return self._publish(receipt, board)

    def _publish(self, receipt, board):
        revision, expected = receipt["revision"], receipt.get("expected")
        self.pin()
        result = self._push(receipt)
        if result["returncode"] == 0:
            self.release(receipt)
            return Snapshot(revision, board)
        try:
            applied, current = self._reconcile(receipt)
        except TeamError as exc:
            raise TeamError("team-write-unknown", "Team publication could not be confirmed; retain the pending operation and reconcile before retrying.") from exc
        if applied:
            self.release(receipt)
            return current
        if current.revision != expected:
            raise TeamError("team-contention", "The team board changed; the pending publication was not applied.")
        rejected = any(line.startswith("!\t") and
                       ("\t[rejected]" in line or "\t[remote rejected]" in line)
                       for line in result["stdout"].splitlines())
        if result.get("timed_out") or result.get("termination_error") or not rejected:
            raise TeamError("team-write-unknown", "Team publication was not confirmed; retain the pending operation until its result can be reconciled.")
        if any(reason in result["stdout"] for reason in ("stale info", "fetch first", "non-fast-forward")):
            raise TeamError("team-contention", "The team board changed; refresh ownership before retrying.")
        raise TeamError("team-unavailable", "Git rejected the team publication; check repository access, hooks and branch policies.")
