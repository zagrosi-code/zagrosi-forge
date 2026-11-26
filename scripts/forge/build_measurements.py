"""Read local observations without treating a measurement as completion evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import re

from . import output, sections, state_first, storage, verification


TOKENS = ("input_tokens", "cached_input_tokens", "output_tokens")
INTEGRATION = "integration"


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _sum(values):
    values = list(values)
    return sum(values) if values and None not in values else None


def _usage(provider, value):
    value = value if isinstance(value, dict) else {}
    input_count = _integer(value.get("input_tokens"))
    output_count = _integer(value.get("output_tokens"))
    if provider == "claude":
        cached = _integer(value.get("cache_read_input_tokens"))
        input_count = _sum((input_count, cached, _integer(value.get("cache_creation_input_tokens"))))
    else:
        cached = _integer(value.get("cached_input_tokens"))
    return dict(zip(TOKENS, [input_count, cached, output_count], strict=True))


def _claude_result(event):
    models = event.get("modelUsage")
    if not isinstance(models, dict) or not models:
        return _usage("claude", event.get("usage"))
    counts = {}
    for key in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens"):
        counts[key] = _sum(_integer(model.get(key)) if isinstance(model, dict) else None for model in models.values())
    return {"input_tokens": _sum(counts[key] for key in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")),
            "cached_input_tokens": counts["cacheReadInputTokens"], "output_tokens": counts["outputTokens"]}


def _events(path):
    with path.open(encoding="utf-8") as stream:
        if path.suffix.lower() == ".jsonl":
            for line in stream:
                if line.strip():
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError("Expected a JSON object per telemetry line.")
                    yield item
        else:
            item = json.load(stream)
            if not isinstance(item, dict):
                raise ValueError("Expected a provider result or one JSON object per JSONL line.")
            yield item


def _telemetry(path):
    """Read one explicit provider result or native session, retaining only counts and identity."""
    identity = None
    provider = None
    complete = False
    identified = True
    totals = None
    messages = {}
    sequence = 0
    for event in _events(path):
        kind = event.get("type")
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if event.get("schema") == "forge-provider-review-v1":
            if type(event.get("returncode")) is not int:
                raise ValueError("The provider report does not record a review execution.")
            provider = event.get("provider")
            native = event.get("native_session_id")
            observed = native or event.get("review_id")
            identified = isinstance(observed, str) and bool(observed)
            identity = observed if identified else hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            counts = _claude_result(event) if provider == "claude" else _usage(provider, event.get("usage"))
            return (provider if native else "review", identity), counts, event.get("success") is True, identified
        if kind == "session_meta" or kind == "thread.started":
            current = payload.get("id") if kind == "session_meta" else event.get("thread_id")
            provider = "codex"
        else:
            current = event.get("session_id")
            if current:
                provider = "claude"
        if current:
            if identity is not None and current != identity:
                raise ValueError("Supply one native session per telemetry file.")
            identity = current
        if kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                totals = _usage("codex", info["total_token_usage"])
        elif kind == "event_msg" and payload.get("type") in {"task_started", "task_complete", "task_aborted"}:
            complete = payload["type"] == "task_complete"
        elif kind == "turn.started":
            complete = False
        elif kind == "turn.completed":
            sequence += 1
            messages[event.get("turn_id") or sequence] = _usage("codex", event.get("usage"))
            complete = True
        elif kind in {"turn.failed", "error"}:
            complete = False
        elif kind == "assistant" and isinstance(event.get("message"), dict):
            message = event["message"]
            if message.get("id") and isinstance(message.get("usage"), dict):
                messages[message["id"]] = _usage("claude", message["usage"])
        elif kind == "result" and provider == "claude":
            totals = _claude_result(event)
            complete = event.get("subtype") == "success" and not event.get("is_error")
    if not isinstance(identity, str) or not identity or not provider:
        raise ValueError("No native session or Forge provider review identity was found.")
    if totals is None:
        totals = {key: _sum(message[key] for message in messages.values()) for key in TOKENS}
    return (provider, identity), totals, complete, identified


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value)
        return date.timestamp() if date.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        return None


@dataclass(frozen=True)
class Check:
    command: tuple[str, ...]
    seconds: float | None
    start: float | None
    finish: float | None
    outcome: str | None


@dataclass
class Session:
    scope: str
    counts: dict
    complete: bool
    review: bool
    identified: bool
    conflicting: bool = False


@dataclass
class Observations:
    # A dual-scope ordinary receipt and a repeated group report share one execution ID.
    runs: dict = field(default_factory=dict)
    scopes: dict = field(default_factory=lambda: defaultdict(set))
    incomplete: set = field(default_factory=set)
    telemetry: dict = field(default_factory=dict)

    def check(self, scope, record):
        if record.get("source") in {"inspection", "attestation", "reported"}:
            return
        run_id = record.get("run_id")
        command = record.get("command")
        if (not isinstance(run_id, str) or not run_id or not isinstance(command, list) or not command
                or not all(isinstance(arg, str) for arg in command) or record.get("outcome") == "pending"
                or type(record.get("exit_code", record.get("returncode"))) is not int):
            self.incomplete.add(scope)
            return
        duration = record.get("seconds")
        if type(duration) not in (int, float) or duration < 0 or not math.isfinite(duration):
            duration = None
        start, finish = (_timestamp(record.get(field)) for field in ("started_at", "finished_at"))
        if start is None or finish is None or start > finish:
            start = finish = None
        measurement = Check(tuple(command), duration, start, finish, record.get("outcome"))
        if run_id in self.runs and self.runs[run_id] != measurement:
            raise ValueError("Conflicting measurements for the same execution ID.")
        self.scopes[scope].add(run_id)
        self.runs[run_id] = measurement

    def observe(self, scope, path, review):
        key, counts, complete, identified = _telemetry(path)
        previous = self.telemetry.get(key)
        incoming = Session(scope, counts, complete, review, identified)
        if previous:
            if previous.scope != scope:
                raise ValueError("The same native session or review was assigned to two scopes.")
            incoming.conflicting = previous.conflicting or previous.counts != counts
            if incoming.conflicting:
                # Overlapping exports of one session cannot be added. Preserve a lower bound.
                incoming.counts = {name: max((n for n in (previous.counts[name], counts[name]) if n is not None),
                                             default=None) for name in TOKENS}
                incoming.complete = False
            else:
                incoming.complete = complete or previous.complete
            incoming.review = review or previous.review
            incoming.identified = previous.identified or identified
        self.telemetry[key] = incoming

    def summary(self, selected, observation_scopes, *, telemetry_scopes=None):
        ids = set().union(*(self.scopes[scope] for scope in selected))
        runs = [self.runs[key] for key in ids]
        commands = Counter(run.command for run in runs)
        repeated = sum(n - 1 for n in commands.values())
        measured = (bool(runs) and not self.incomplete.intersection(selected)
                    and all(self.scopes[scope] for scope in observation_scopes))
        elapsed = _sum(run.seconds for run in runs)
        span = None
        if measured and all(run.start is not None and run.finish is not None for run in runs):
            span = round(max(run.finish for run in runs) - min(run.start for run in runs), 6)
        sessions = [row for row in self.telemetry.values() if row.scope in selected]
        reviews = [row for row in sessions if row.review]
        expected = observation_scopes if telemetry_scopes is None else telemetry_scopes
        review_complete = bool(reviews) and expected <= {row.scope for row in reviews}
        usage_complete = bool(sessions) and expected <= {row.scope for row in sessions}
        outcomes = Counter(run.outcome or "unknown" for run in runs)
        result = {"observed_checks": len(runs), "check_outcomes": dict(sorted(outcomes.items())), "checks": len(runs) if measured else None,
                  "observed_repeated_checks": repeated, "repeated_checks": repeated if measured else None,
                  "repeated_commands": [{"command": list(argv), "executions": count}
                                        for argv, count in sorted(commands.items()) if count > 1],
                  "observed_check_seconds": round(sum(run.seconds for run in runs if run.seconds is not None), 6),
                  "check_wall_span_seconds": span,
                  "check_seconds": round(elapsed, 6) if measured and elapsed is not None else None,
                  "observed_review_rounds": len(reviews),
                  "review_rounds": len(reviews) if review_complete and all(row.complete and row.identified for row in reviews) else None}
        for key in TOKENS:
            values = [row.counts[key] for row in sessions]
            result['observed_' + key] = sum(n for n in values if n is not None)
            result[key] = _sum(values) if usage_complete and all(row.complete for row in sessions) else None
        return result


def _read_checks(planning, names, observations):
    folder = planning / "implementation/verification"
    for scope in [*names, INTEGRATION]:
        for suffix in ("", "-compatibility") if scope != INTEGRATION else ("",):
            stem = scope + suffix
            current = folder / f"{stem}.json"
            history = folder / "history" / stem
            paths = [current] if verification._check_managed_path(planning, current) else []
            if verification._check_managed_path(planning, history, directory=True):
                paths.extend(sorted(history.glob("*.json")))
            for path in paths:
                if not verification._check_managed_path(planning, path):
                    continue
                try:
                    receipt = storage.load_json(path)
                    if not isinstance(receipt, dict):
                        observations.incomplete.add(scope)
                        continue
                    if suffix:
                        prior = receipt.get("prior_attempts", [])
                        if not isinstance(prior, list) or not all(isinstance(row, dict) for row in prior):
                            observations.incomplete.add(scope)
                            prior = []
                        attempts = [row.get("result") for row in prior]
                        attempts.extend(receipt[key] for key in ("baseline", "candidate") if key in receipt)
                    else:
                        attempts = [receipt]
                    for item in attempts:
                        if isinstance(item, dict):
                            observations.check(scope, item)
                        else:
                            observations.incomplete.add(scope)
                except (UnicodeError, json.JSONDecodeError):
                    observations.incomplete.add(scope)
    if state_first.active(planning):
        status = state_first.load_status(planning)
        for name, entry in status["sections"].items():
            for key in ("baseline", "check", "candidate"):
                if isinstance(entry.get(key), dict):
                    observations.check(name, entry[key])
        if isinstance(status.get("final"), dict):
            observations.check(INTEGRATION, status["final"])
        measurements = folder / "measurements"
        if verification._check_managed_path(planning, measurements, directory=True):
            for path in sorted(measurements.glob("*.json")):
                verification._check_managed_path(planning, path, single_link=True)
                row = storage.load_json(path)
                observations.check(row.get("section") or INTEGRATION, row)


def _groups(files, planning, known, observations):
    from . import team_git, team_plans, team_state

    if not files:
        return []
    try:
        prepared = team_plans.validate(planning)
        if prepared:
            marker = storage.load_json(planning / team_plans.MARKER)
            repo = team_git.Repository.discover(Path(marker["target"]))
            canonical, digest = prepared["path"], prepared["digest"]
        else:
            repo = team_git.Repository.discover(planning)
            canonical = planning.relative_to(repo.root).as_posix()
            records, _ = team_plans._bundle(repo, canonical)
            digest = team_plans._digest(records)
    except team_state.TeamError as exc:
        raise ValueError(f"Reading a group requires its committed plan: {exc}") from exc
    groups = []
    trees = {}
    for file in files:
        package = storage.load_json(Path(file))
        identity = package.get("handoff") if isinstance(package, dict) else None
        if not isinstance(identity, dict) or identity.get("schema") != "forge-section-group-v1":
            raise ValueError("Expected a Forge section-group handoff or group verification result.")
        if not all(isinstance(identity.get(name), str) and re.fullmatch(pattern, identity[name]) for name, pattern in
                   (("commit_sha", r"[0-9a-f]{40}([0-9a-f]{24})?"), ("tree_sha", r"[0-9a-f]{40}([0-9a-f]{24})?"),
                    ("plan_digest", r"[0-9a-f]{64}"))):
            raise ValueError("The group is missing its commit, tree or plan identity.")
        if identity.get("pull_request") is not None and not isinstance(identity["pull_request"], str):
            raise ValueError("The group PR identifier must be text.")
        members = identity.get("sections")
        if (not isinstance(members, list) or not members or not all(isinstance(name, str) for name in members)
                or set(members) - known):
            raise ValueError("The group references sections outside this plan.")
        if canonical != identity.get("plan_path") or digest != identity["plan_digest"]:
            raise ValueError("The group belongs to a different canonical plan or plan revision.")
        commit = identity["commit_sha"]
        if commit not in trees:
            try:
                trees[commit] = team_git._output(repo.root, "rev-parse", "--verify", commit + "^{tree}")
            except team_state.TeamError as exc:
                raise ValueError("The group commit is unavailable in this checkout.") from exc
        if trees[commit] != identity["tree_sha"]:
            raise ValueError("The group tree differs from its recorded Git commit.")
        key = ("group", identity["plan_digest"], identity["commit_sha"], identity["tree_sha"], tuple(sorted(members)),
               identity.get("target_path"), identity.get("pull_request"))
        if isinstance(package.get("verification"), dict):
            verification_result = package["verification"]
            failed = (verification_result.get("returncode") != 0 or verification_result.get("snapshot_error")
                      or verification_result.get("termination_error"))
            if verification_result.get("timed_out"):
                outcome = "timed_out"
            elif failed or package.get("success") is False:
                outcome = "failed"
            else:
                outcome = "passed" if package.get("success") is True else None
            observations.check(key, {**verification_result, "outcome": outcome})
        groups.append((key, set(members), identity.get("pull_request")))
    return groups


def run(args):
    try:
        planning = Path(args.planning_dir).resolve()
        known = (set(state_first.load_status(planning)["sections"]) if state_first.active(planning)
                 else set(sections.check_section_progress(planning)["sections"]))
        if not known:
            raise ValueError("Select a Forge planning directory with sections.")
        observations = Observations()
        _read_checks(planning, known, observations)
        groups = _groups(args.group, planning, known, observations)
        group_members = set().union(*(members for _, members, _ in groups)) if groups else known
        selected = set(args.section) if args.section else group_members
        if not selected or selected - group_members:
            raise ValueError("Choose sections in the selected plan or section group.")
        pr_members = defaultdict(set)
        pr_groups = defaultdict(set)
        for key, members, pr in groups:
            if pr:
                pr_members[pr].update(members)
                if members <= selected:
                    pr_groups[pr].add(key)
        direct_scopes = {"pr:" + pr for pr in pr_members}
        for paths, review in ((args.usage, False), (args.review, True)):
            for scope, path in paths:
                if scope not in known | {INTEGRATION} | direct_scopes:
                    raise ValueError("Attribute telemetry to a known section, integration or pr:<group PR>.")
                observations.observe(scope, Path(path), review)
        scope_keys = selected | {key for key, members, _ in groups if members <= selected}
        if not groups and not args.section:
            scope_keys.add(INTEGRATION)
        prs = {}
        for pr, members in sorted(pr_members.items()):
            selected_members = members & selected
            if not selected_members:
                continue
            # A whole-PR session cannot be charged to a filtered subset of that PR.
            telemetry = {"pr:" + pr} if members <= selected else set()
            scope_keys.update(telemetry)
            attributed = observations.summary(pr_groups[pr] | telemetry, pr_groups[pr], telemetry_scopes=telemetry)
            prs[pr] = {**attributed, "scope": "direct_pr_observations", "sections": sorted(selected_members),
                       "section_history": observations.summary(selected_members, selected_members)}
        unique_groups = {(key, pr): members for key, members, pr in groups if members <= selected}
        result = {"schema": "forge-build-report-v1", "success": True,
                  "summary": observations.summary(scope_keys, selected),
                  "sections": {name: observations.summary({name}, {name}) for name in sorted(selected)},
                  "integration": observations.summary({INTEGRATION}, {INTEGRATION}) if INTEGRATION in scope_keys else None,
                  "pull_requests": prs,
                  "groups": [{"commit_sha": key[2], "tree_sha": key[3], "plan_digest": key[1], "target_path": key[5],
                              "sections": sorted(members), "pull_request": pr,
                              "verification": observations.summary({key}, {key})}
                             for (key, pr), members in unique_groups.items()]}
        return output.print_json(result)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return output.print_json({"success": False, "error": str(exc)}, 1)
