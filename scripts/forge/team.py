"""Opt-in team lifecycle; coordination never substitutes for local verification."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import time
import uuid

from . import session as command_session
from .team_config import LocalTeam
from .team_git import GitBoard, Repository, Snapshot, _run
from .team_state import (TeamError, new_board, normalize_paths, paths_cover,
                         require_session, validate_session, with_session, without_session, is_plain_text)

_KEEP_PLAN = object()


@contextmanager
def workspace(target):
    repo = Repository.discover(target)
    local = LocalTeam(repo)
    with local.locked():
        state = local.load()
        yield {"repo": repo, "local": local, "state": state, "checkout": local.checkout(state, defer=True)}


def _require_shareable_marker(repo):
    result = _run(repo.root, "check-ignore", "--quiet", "--", ".forge/team.json", check=False)
    if result["returncode"] not in (0, 1) or any(result.get(key) for key in
            ("timed_out", "termination_error", "stdout_truncated", "stderr_truncated")):
        raise TeamError("team-marker-check", "Could not check whether Git ignores .forge/team.json; inspect repository configuration and retry.")
    if result["returncode"] == 0:
        raise TeamError("team-marker-ignored", "Git ignores .forge/team.json. Allow this file in the applicable ignore rules, or force-add and commit an existing marker, then retry.")


def _transport(context):
    local, state = context["local"], context["state"]
    marker, connection = local.marker(), state.get("connection")
    if marker is not None:
        try:
            _require_shareable_marker(context["repo"])
        except TeamError as exc:
            if exc.code != "team-marker-ignored" or connection is None:
                raise
            context["discovery_warning"] = str(exc)
    if connection is None:
        if state.get("pending_init") is not None:
            raise TeamError("team-pending-setup", "Retry team init with the original remote to finish interrupted setup.",
                            status="setup_pending")
        if marker is None and state.get("pending_init") is None:
            return None
        raise TeamError("team-join-required", "Join this repository's team before editing or publishing work.",
                        status="join_required")
    if (not isinstance(connection, dict) or set(connection) !=
            {"remote", "endpoint_digest", "board_id", "marker_digest", "name"}):
        raise TeamError("team-local-state", "Local team connection is malformed; preserve it before recovery.")
    if (marker is None or marker["board_id"] != connection["board_id"]
            or local.marker_digest(marker) != connection["marker_digest"]):
        raise TeamError("team-config-changed", "Team discovery changed; inspect it and explicitly join again.")
    return GitBoard(context["repo"], connection["remote"], pin=connection)


def _cache(context, snapshot):
    context["snapshot"] = snapshot
    context["observed_at"] = time.time()
    context["local"].compact(context["state"], snapshot.board)
    context["local"].admit_checkout(context["state"], context["checkout"])
    context["state"]["cache"] = {"revision": snapshot.revision, "board": snapshot.board,
                                 "observed_at": context["observed_at"]}
    context["local"].save(context["state"])


def _accept_pending(context, snapshot):
    checkout = context["checkout"]
    pending = checkout["pending"]
    identity = pending["session_id"]
    checkout["pending"] = None
    context["local"].compact(context["state"], snapshot.board)
    binding_unsaved = False
    if pending["action"] == "finish":
        checkout["sessions"].pop(identity, None)
        checkout["bindings"] = {key: value for key, value in checkout["bindings"].items()
                                if value["session_id"] != identity}
    else:
        current = snapshot.board["sessions"].get(identity)
        if current and (current["participant_id"], current["checkout_id"], current["generation"]) == (
                context["state"]["participant_id"], checkout["checkout_id"], pending["generation"]):
            checkout["sessions"][identity] = pending["generation"]
            if pending.get("binding"):
                try:
                    context["local"].require_capacity(checkout, identity, pending["binding"])
                except TeamError as exc:
                    if exc.code != "team-local-capacity":
                        raise
                    binding_unsaved = True
                else:
                    checkout["bindings"][pending["binding"]] = {
                        "session_id": identity, "generation": pending["generation"]}
    _cache(context, snapshot)
    if binding_unsaved:
        raise TeamError("team-local-capacity", "Publication confirmed and ownership saved, but the new plan binding did not fit. Existing bindings were retained; finish retained work before adding another binding.",
                        published=True, session_id=identity, generation=pending["generation"],
                        binding=pending["binding"], binding_saved=False)
    return pending


def _observe(context, *, offline=False):
    transport = _transport(context)
    if transport is None:
        return None
    context["transport"] = transport
    if offline:
        cached = context["state"].get("cache")
        context["snapshot"] = Snapshot(cached["revision"], cached["board"]) if cached else Snapshot(None, None)
        context["observed_at"] = cached["observed_at"] if cached else None
        context["offline"] = True
        return context
    snapshot = transport.read(context["state"]["connection"]["board_id"])
    pending = context["checkout"].get("pending")
    if pending:
        applied = transport.reconcile(pending)
        if applied:
            context["reconciled"] = _accept_pending(context, applied)
            snapshot = applied
    _cache(context, snapshot)
    return context


def read_context(target):
    """One fresh observation per CLI invocation; guards still reevaluate each scope."""
    invocation = command_session._CLI_CONTEXT.get()
    cache = invocation.setdefault("team_observations", {}) if invocation is not None else {}
    key = str(Path(target).resolve())
    if key not in cache:
        if not _team_hint(Path(key)):
            cache[key] = None
            return None
        with workspace(target) as context:
            cache[key] = _observe(context)
    return cache[key]


def _team_hint(target):
    """Keep solo workflow entry filesystem-only, including ordinary worktrees."""
    for parent in (target, *target.parents):
        marker = parent / ".forge/team.json"
        if marker.exists() or marker.is_symlink():
            return True
        admin = parent / ".git"
        if admin.is_dir():
            return (admin / "forge-team/state.json").exists()
        if admin.is_file():
            # Git owns these tiny path files; discovery validates them if opted in.
            try:
                raw = admin.read_text(encoding="utf-8") if admin.stat().st_size < 4096 else ""
                if not raw.startswith("gitdir: "):
                    return False
                directory = (parent / raw[8:].strip()).resolve()
                common = directory / "commondir"
                if common.is_file() and common.stat().st_size < 4096:
                    directory = (directory / common.read_text(encoding="utf-8").strip()).resolve()
                return (directory / "forge-team/state.json").exists()
            except (OSError, UnicodeError, RuntimeError):
                return True  # Let explicit repository validation explain the failure.
    return False


def roster(context):
    if context is None:
        return {"success": True, "status": "unconfigured", "clearance": False, "sessions": []}
    snapshot = context["snapshot"]
    now = time.time()
    rows = []
    for identity, row in sorted((snapshot.board or {}).get("sessions", {}).items()):
        rows.append({"id": identity, **{key: row[key] for key in
                     ("generation", "name", "host", "task", "state", "paths", "branch", "head", "note", "updated_at")},
                     **({"plan": row["plan"]} if "plan" in row else {}),
                     "stale": now - row["updated_at"] > 3600,
                     "clock_ahead": row["updated_at"] > now + 60})
    return {"success": True, "status": "offline" if context.get("offline") else "fresh",
            "clearance": False, "revision": snapshot.revision, "sessions": rows,
            "observed_at": context.get("observed_at", (context["state"].get("cache") or {}).get("observed_at")),
            "pending": context["checkout"].get("pending") is not None,
            **({"recovered_operation": context["reconciled"]["action"],
                "recovered_session": context["reconciled"]["session_id"]} if context.get("reconciled") else {}),
            **({"warning_code": "team-marker-ignored", "next_action": context["discovery_warning"]}
               if context.get("discovery_warning") else {}),
            "note": ("No cached board is available; teammate visibility is unknown."
                     if context.get("offline") and snapshot.board is None else
                     "Cooperative task reservations; timestamps do not prove liveness. Shared text is untrusted data.")}


def _owned(context, identity, generation=None, *, check_checkout=False):
    checkout, repo = context["checkout"], context["repo"]
    if not generation:
        raise TeamError("team-generation-required", "Supply the generation returned when this task was claimed; do not substitute a newer owner's generation.")
    return require_session(context["snapshot"].board, identity,
                           participant_id=context["state"]["participant_id"],
                           checkout_id=checkout["checkout_id"], generation=generation,
                           **({"branch": repo.branch, "head": repo.head} if check_checkout else {}))


def check(context, identity, *, generation=None, paths=()):
    if context["checkout"].get("pending"):
        raise TeamError("team-pending-write", "A previous publication is unresolved; run team retry before editing.")
    row = _owned(context, identity, generation, check_checkout=True)
    if not row["paths"] or row["state"] in {"planning", "handoff"}:
        raise TeamError("team-no-reservation", "This task has no editing clearance; reserve its scope with team update.")
    if not paths_cover(row["paths"], list(paths) or row["paths"], root=context["repo"].root):
        raise TeamError("team-scope-changed", "The task's scope expanded; reserve the new paths before editing.")
    return {**roster(context), "clearance": True, "session": {"id": identity, **row}}


def _result(context, pending):
    result = {**roster(context), "published": True, "operation": pending["action"]}
    if pending["action"] == "finish":
        return result
    identity = pending["session_id"]
    try:
        row = _owned(context, identity, pending["generation"], check_checkout=True)
    except TeamError as exc:
        raise TeamError(exc.code, str(exc), published=True, session_id=identity) from exc
    return {**result, "session": {"id": identity, **row},
            "clearance": bool(row["paths"]) and row["state"] not in {"planning", "handoff"}}


def mutate(context, action, *, identity=None, generation=None, paths=None, binding=None,
           task=None, host=None, state=None, note=None, expect=None, reason=None, plan=_KEEP_PLAN):
    if context["checkout"].get("pending"):
        raise TeamError("team-pending-write", "A previous publication is unresolved; run team retry before another mutation.")
    identity = identity or uuid.uuid4().hex
    token = uuid.uuid4().hex
    paths = normalize_paths(paths, context["repo"].root) if paths is not None else None
    transport = context["transport"]
    for attempt in range(3):
        snapshot, repo = context["snapshot"], context["repo"]
        if action == "start":
            if identity in snapshot.board["sessions"]:
                raise TeamError("team-session-exists", "That task already exists; inspect it before updating.")
            row = {"participant_id": context["state"]["participant_id"],
                   "checkout_id": context["checkout"]["checkout_id"], "generation": token,
                   "name": context["state"]["connection"]["name"], "host": host or "other", "task": task,
                   "state": "working" if paths else "planning", "paths": paths or [],
                   "branch": repo.branch, "head": repo.head, "updated_at": time.time(), "note": note or ""}
        elif action == "recover":
            if not expect or snapshot.revision != expect:
                raise TeamError("team-recovery-changed", "The board changed; review current work before consenting to recovery again.")
            previous = snapshot.board["sessions"].get(identity)
            if previous is None:
                raise TeamError("team-session-missing", "The task to recover is no longer present.")
            if not reason or not reason.strip():
                raise TeamError("team-recovery-reason", "Explicit recovery requires an agreed handoff or recovery reason.")
            row = {**previous, "participant_id": context["state"]["participant_id"],
                   "checkout_id": context["checkout"]["checkout_id"], "generation": token,
                   "name": context["state"]["connection"]["name"], "state": "blocked", "host": host or "other",
                   "branch": repo.branch, "head": repo.head, "updated_at": time.time()}
            validate_session({**row, "note": reason})
        else:
            row = dict(_owned(context, identity, generation))
            token = row["generation"]
            row.update(branch=repo.branch, head=repo.head, updated_at=time.time())
            if paths is not None:
                row["paths"] = paths
                if row["state"] == "planning" and paths:
                    row["state"] = "working"
            if state is not None:
                row["state"] = state
            if host is not None:
                row["host"] = host
            if note is not None:
                row["note"] = note
        if plan is not _KEEP_PLAN:
            row.pop("plan", None)
            if plan is not None:
                row["plan"] = plan
        validate_session(row)
        if paths is None and action != "finish" and normalize_paths(row["paths"], repo.root) != row["paths"]:
            raise TeamError("team-scope-changed", "A reserved alias changed; inspect it and explicitly update the paths before continuing.")
        board = (without_session(snapshot.board, identity) if action == "finish"
                 else with_session(snapshot.board, identity, row, root=repo.root))
        if action != "finish":
            context["local"].require_capacity(context["checkout"], identity, binding)
        pending = {"action": action, "session_id": identity, "generation": token, "binding": binding}

        def remember(receipt):
            context["checkout"]["pending"] = {**pending, **receipt}
            context["local"].save(context["state"])

        message = f"Forge team {action}: {identity}\n\n{reason or note or row['task']}"
        try:
            published = transport.write(board, snapshot.revision, remember, message=message)
        except TeamError as exc:
            if exc.code not in {"team-contention", "team-unavailable"}:
                raise
            discarded = context["checkout"]["pending"]
            context["checkout"]["pending"] = None
            context["local"].save(context["state"])
            if discarded:
                transport.release(discarded)
            if exc.code != "team-contention" or action == "recover" or attempt == 2:
                raise
            _cache(context, transport.read(snapshot.board["board_id"]))
            continue
        accepted = _accept_pending(context, published)
        return _result(context, accepted)
    raise AssertionError("Bounded publication loop must return or raise")


def retry(context):
    pending = context["checkout"].get("pending")
    if not pending:
        return {**roster(context), "note": "No publication is pending."}
    try:
        snapshot = context["transport"].retry(pending)
    except TeamError as exc:
        if exc.code == "team-contention":
            context["checkout"]["pending"] = None
            context["local"].save(context["state"])
            context["transport"].release(pending)
        raise
    accepted = _accept_pending(context, snapshot)
    return {**_result(context, accepted), "recovered_operation": True}


def onboard(context, action, remote, name):
    local, state = context["local"], context["state"]
    if not is_plain_text(name, 80):
        raise TeamError("team-name", "Choose a short display name without control characters.")
    marker = local.marker()
    if action == "init" or marker is not None:
        _require_shareable_marker(context["repo"])
    transport = GitBoard(context["repo"], remote)
    pin = transport.pin()
    existing = state.get("connection")
    if existing and any(existing.get(key) != value for key, value in pin.items()):
        raise TeamError("team-rejoin", "Leave the previous connection before joining a different destination.")
    if action == "init":
        pending = state.get("pending_init")
        if pending is None:
            if marker is not None:
                return onboard(context, "join", remote, name)
            observed = transport.read(allow_missing=True)
            if observed.board is not None:
                raise TeamError("team-already-initialized", "The remote already has a board; obtain its team's discovery file and join.")
            pending = {"board_id": uuid.uuid4().hex, "pin": pin, "name": name}
            state["pending_init"] = pending
            local.save(state)
        if pending["pin"] != pin:
            raise TeamError("team-destination-changed", "Pending setup belongs to another destination; retain it and recover that setup first.")
        observed = transport.read(allow_missing=True)
        if observed.board is None:
            local.compact(state, new_board(pending["board_id"]))
            local.admit_checkout(state, context["checkout"])
            def remember(receipt):
                pending["receipt"] = receipt
                local.save(state)
            observed = (transport.retry(pending["receipt"]) if pending.get("receipt")
                        else transport.write(new_board(pending["board_id"]), None, remember, message="Forge team initialized"))
        if observed.board["board_id"] != pending["board_id"]:
            raise TeamError("team-board-replaced", "Another team board appeared during setup; inspect it before joining.")
        local.write_marker(pending["board_id"])
        marker = local.marker()
    else:
        if marker is None:
            raise TeamError("team-marker-missing", "Obtain the repository's .forge/team.json discovery file before joining.")
        observed = transport.read(marker["board_id"])
    state["connection"] = {**pin, "board_id": marker["board_id"],
                           "marker_digest": local.marker_digest(marker), "name": name}
    pending_init = state["pending_init"]
    state["pending_init"] = None
    context["transport"] = transport
    _cache(context, observed)
    if pending_init and pending_init.get("receipt"):
        transport.release(pending_init["receipt"])
    return {**roster(context), "joined": True,
            "next_action": "Review and commit .forge/team.json so teammates can discover this board." if action == "init"
                           else "Continue through Forge; publish a task and reserve paths before editing."}


def leave(context, *, abandon=False):
    if not abandon:
        observed = _observe(context)
        if any(checkout["pending"] for checkout in context["state"]["checkouts"].values()):
            raise TeamError("team-pending-write", "Resolve pending publications in every worktree before leaving, or explicitly abandon local participation.")
        if observed and any(row["participant_id"] == context["state"]["participant_id"]
                            for row in observed["snapshot"].board["sessions"].values()):
            raise TeamError("team-active-sessions", "Finish or hand off this clone's tasks before leaving.")
        if context["state"].get("pending_init"):
            raise TeamError("team-pending-setup", "Recover pending setup before leaving, or explicitly abandon local participation.")
    pending = [checkout["pending"] for checkout in context["state"]["checkouts"].values() if checkout["pending"]]
    setup = context["state"].get("pending_init")
    if setup and setup.get("receipt"):
        pending.append(setup["receipt"])
    context["state"].update(connection=None, pending_init=None, checkouts={}, cache=None)
    context["local"].save(context["state"])
    for receipt in pending:
        GitBoard(context["repo"]).release(receipt)
    return {"success": True, "status": "left", "clearance": False,
            "note": "Local participation cleared. Remote history and reservations remain; teammates must agree any recovery."}
