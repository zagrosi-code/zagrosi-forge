"""Stage outcomes and named CI or acceptance requirements for one tracked pull request."""
from __future__ import annotations

from . import team_state


OUTCOMES = {
    "review": {"approved", "changes_requested", "pending", "dismissed"},
    "merge": {"merged", "open", "closed"},
    "ci": {"passed", "failed", "skipped", "pending"},
    "deploy": {"passed", "failed", "skipped", "pending"},
    "live": {"passed", "failed", "skipped", "pending"},
    "acceptance": {"accepted", "rejected", "skipped", "pending"},
}
ENV_STAGES = {"deploy", "live", "acceptance"}


def latest(events):
    # Observations can be imported out of order. On equal timestamps, the last entry wins.
    return max(reversed(events), key=lambda event: event["observed_at"], default=None)


def review_basis(snapshot):
    # Landing preserves approval of the source head; changing an upstream revision invalidates it.
    return {key: value for key, value in snapshot.items() if key != "merged_sha"}


def required_names(row, stage, environment):
    if stage == "ci":
        return row.get("required_ci", ())
    if stage == "acceptance":
        return row.get("required_acceptance", {}).get(environment, ())
    return ()


def _name(value):
    if not team_state.is_plain_text(value, 200):
        raise ValueError("Use a nonempty, single-line requirement name of up to 200 characters.")
    return value.strip()


def configure(row, stage, environment, names):
    """Replace one requirement list. Keep the observations when requirements change."""
    names = [_name(value) for value in names]
    if len(names) != len(set(names)):
        raise ValueError("Each requirement name must occur once in the list.")
    if stage == "ci":
        if environment is not None:
            raise ValueError("CI requirements apply to the PR. Omit --environment.")
        row["required_ci"] = names
    else:
        if not team_state.is_plain_text(environment, 200):
            raise ValueError("Acceptance requirements need a named --environment.")
        by_environment = row.setdefault("required_acceptance", {})
        if names:
            by_environment[environment.strip()] = names
        else:
            by_environment.pop(environment.strip(), None)


def observation_name(row, stage, environment, value):
    name = _name(value)
    if name not in required_names(row, stage, environment):
        raise ValueError("A named observation must match a declared CI check or acceptance sign-off in this scope.")
    return name


def _matches(event, snapshot, basis):
    stage = event["stage"]
    if stage == "merge":
        return event["head_sha"] == snapshot["head_sha"]
    if stage == "review":
        return event["sha"] == snapshot["head_sha"] and event["basis"] == basis
    if stage == "ci":
        if snapshot["merged_sha"]:
            return event["ref"] == snapshot["base_ref"] and event["sha"] == snapshot["merged_sha"]
        return event["ref"] == snapshot["head_ref"] and event["basis"] == basis
    return event["sha"] == (snapshot["merged_sha"] or snapshot["head_sha"])


def _observed_results(events, snapshot, environment):
    """Index the latest applicable and stale evidence per name in one pass."""
    current, stale = {}, {}
    basis = review_basis(snapshot)
    for event in events:
        stage = event["stage"]
        scope = environment if stage in ENV_STAGES else None
        if event["environment"] != scope:
            continue
        key = (stage, event.get("name"))
        bucket = current if _matches(event, snapshot, basis) else stale
        previous = bucket.get(key)
        if previous is None or event["observed_at"] >= previous["observed_at"]:
            bucket[key] = event
    for key, event in stale.items():
        if key not in current:
            current[key] = {**event, "previous_outcome": event["outcome"], "outcome": "stale"}
    return current


def stage_report(row, snapshot, environment):
    observed = _observed_results(row["observations"], snapshot, environment)
    result = {}
    for stage in OUTCOMES:
        names = required_names(row, stage, environment)
        if not names:
            result[stage] = observed.get((stage, None), {"outcome": "unknown"})
            continue
        items = {name: observed.get((stage, name), {"outcome": "missing"}) for name in names}
        outcomes = {item["outcome"] for item in items.values()}
        priority = ("failed", "rejected", "stale", "missing", "pending", "skipped", "passed", "accepted")
        result[stage] = {"outcome": next(value for value in priority if value in outcomes), "requirements": items}
    return result
