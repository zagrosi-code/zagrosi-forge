"""Bounded, advisory overlaps between declared inputs and reserved writes."""
from __future__ import annotations

from pathlib import Path

from . import team_state as _state


def _observe(path: str, root: Path):
    """Retain the literal even when its local alias cannot be inspected safely."""
    try:
        aliases = _state.normalize_paths([path], root)
    except _state.TeamError:
        return [path], None, False
    return aliases, _state._inode(root, path), True


class _Scope:
    """One actor scope, indexed for portable, alias and inode witnesses."""

    def __init__(self, paths, observations):
        self.literals, self.aliases, self.inodes = {}, {}, {}
        for path in sorted(paths):
            aliases, inode, _ = observations[path]
            self.literals.setdefault(_state._portable(path), path)
            for alias in aliases:
                self.aliases.setdefault(_state._portable(alias), (path, alias))
            if inode is not None:
                self.inodes.setdefault(inode, path)
        self.literal_keys = sorted(self.literals)
        self.alias_keys = sorted(self.aliases)

    def match(self, paths, observations):
        """Return actor path, peer path, basis and optional actor/peer aliases."""
        for path in sorted(paths):
            actor = _state._overlapping_claim(self.literals, self.literal_keys, _state._portable(path))
            if actor is not None:
                return actor, path, "portable_path", None
            aliases, inode, _ = observations[path]
            for alias in aliases:
                match = _state._overlapping_claim(self.aliases, self.alias_keys, _state._portable(alias))
                if match is not None:
                    actor, actor_alias = match
                    return actor, path, "local_alias", (actor_alias, alias)
            if inode is not None and inode in self.inodes:
                return self.inodes[inode], path, "local_inode", None
        return None


def project(board, session_id, *, root: Path, now, offline: bool = False) -> dict:
    """Observe a validated board once; never grant clearance or mutate its rows."""
    reason = None
    if offline:
        reason = "offline"
    elif board is not None and board["version"] == 1:
        reason = "protocol-v1"
    elif board is None or session_id not in board["sessions"]:
        reason = "no-session"
    if reason is not None:
        return {"status": "unavailable", "reason": reason, "warnings": [],
                "omitted_warnings": None, "unknown_sessions": None,
                "alias_issues": {"count": None, "first": None}}

    sessions = board["sessions"]
    observations = {}
    unknown, issue_count, first_issue = 0, 0, None
    for identity, row in sorted(sessions.items()):
        dependencies = row.get("dependencies", {})
        unknown += not dependencies.get("complete", False)
        for path in sorted(set(row["paths"]) | set(dependencies.get("paths", []))):
            if path not in observations:
                observations[path] = _observe(path, root)
            if not observations[path][2]:
                issue_count += 1
                if first_issue is None:
                    first_issue = {"session_id": identity, "path": path}

    actor = sessions[session_id]
    reads = _Scope(actor.get("dependencies", {}).get("paths", []), observations)
    writes = _Scope(actor["paths"], observations)
    warnings, groups = [], 0
    for identity, peer in sorted(sessions.items()):
        if identity == session_id:
            continue
        for direction, scope, paths in (
            ("reads_peer_writes", reads, peer["paths"]),
            ("writes_peer_reads", writes, peer.get("dependencies", {}).get("paths", [])),
        ):
            match = scope.match(paths, observations)
            if match is None:
                continue
            groups += 1
            if len(warnings) == 5:
                continue
            actor_path, peer_path, basis, aliases = match
            actor_reads = direction == "reads_peer_writes"
            alias_paths = None
            if aliases is not None:
                actor_alias, peer_alias = aliases
                alias_paths = {"dependency": actor_alias if actor_reads else peer_alias,
                               "write": peer_alias if actor_reads else actor_alias}
            warnings.append({"session_id": identity, "name": peer["name"],
                             "stale": now - peer["updated_at"] > 3600,
                             "direction": direction,
                             "dependency_path": actor_path if actor_reads else peer_path,
                             "write_path": peer_path if actor_reads else actor_path,
                             "basis": basis, "alias_paths": alias_paths})
    return {"status": "partial" if unknown or issue_count else "observed", "reason": None,
            "warnings": warnings, "omitted_warnings": groups - len(warnings),
            "unknown_sessions": unknown, "alias_issues": {"count": issue_count, "first": first_issue}}
