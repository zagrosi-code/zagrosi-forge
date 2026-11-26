"""PR stacks and source-attributed delivery observations shared by local worktrees."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import cache
import os
from pathlib import Path
import re
import subprocess

from . import delivery_stages as stages
from . import group_evidence, output, storage, team_state
from .team_git import _REPOSITORY_ENV


class Repository:
    def __init__(self, target):
        self.target = target
        self.refs = {}
        self.env = {key: value for key, value in os.environ.items() if key not in _REPOSITORY_ENV}
        self.env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
        common = self.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.removesuffix("\n")
        self.ledger = Path(common) / "forge-delivery.json"

    def git(self, *args, check=True):
        result = subprocess.run(
            ["git", "--no-optional-locks", "--no-replace-objects", *args], cwd=self.target,
            capture_output=True, encoding="utf-8", errors="surrogateescape", env=self.env,
        )
        if check and result.returncode:
            raise ValueError(result.stderr.strip() or f"Git could not read {args[0]}.")
        return result

    def ref(self, name):
        if name not in self.refs:
            if not name.startswith(("refs/heads/", "refs/remotes/")) or self.git("check-ref-format", name, check=False).returncode:
                raise ValueError(f"Use a full branch or remote-tracking ref: {name}")
            result = self.git("rev-parse", "--verify", "--quiet", name + "^{commit}", check=False)
            self.refs[name] = result.stdout.strip() if result.returncode == 0 else None
        return self.refs[name]

    def commit(self, value):
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value):
            raise ValueError("Supply the full lowercase Git commit SHA from the evidence.")
        resolved = self.git("rev-parse", "--verify", value + "^{commit}").stdout.strip()
        if resolved != value:
            raise ValueError("The evidence must identify a commit object directly.")
        return resolved

    def tree(self, sha):
        return self.git("rev-parse", "--verify", sha + "^{tree}").stdout.strip()

    def includes(self, tip, commit):
        return self.git("merge-base", "--is-ancestor", commit, tip, check=False).returncode == 0


def _provenance(args):
    if not args.source.strip() or not args.actor.strip():
        raise ValueError("Identify the evidence source and the actor who produced it.")
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return {"source": args.source.strip(), "actor": args.actor.strip(),
            "observed_at": group_evidence.utc_time(args.observed_at) if args.observed_at else now}


def _parents(prs, key):
    chain = []
    seen = {key}
    parent = prs[key]["parent"]
    while parent is not None:
        if parent not in prs or parent in seen:
            raise ValueError(f"Unknown or cyclic parent PR {parent}.")
        chain.append(parent)
        seen.add(parent)
        parent = prs[parent]["parent"]
    return chain


def _identity(repo, row):
    latest = stages.latest([e for e in row["observations"] if e["stage"] == "merge"])
    if latest and latest["outcome"] == "merged":
        # Hosted PRs close on landing. A branch with the same name can later be reused.
        return {**latest["basis"], "merged_sha": latest["sha"]}
    return {"head_ref": row["head_ref"], "head_sha": repo.ref(row["head_ref"]),
            "base_ref": row["base_ref"], "base_sha": repo.ref(row["base_ref"]), "merged_sha": None}


def _snapshot(prs, key, identities):
    identity = identities(key)
    if "ancestors" in identity:
        return identity
    ancestors = []
    for parent in _parents(prs, key):
        upstream = identities(parent)
        ancestors.append({"pr": parent, **{k: v for k, v in upstream.items() if k != "ancestors"}})
        if "ancestors" in upstream:
            ancestors.extend(upstream["ancestors"])
            break
    return {**identity, "ancestors": ancestors}


def _check_test_subject(repo, snapshot, tested):
    head, base = snapshot["head_sha"], snapshot["base_sha"]
    if not head or not base:
        raise ValueError("A tracked head or base is unavailable. Retarget missing refs before testing.")
    if tested == head:
        if not repo.includes(head, base):
            raise ValueError("The tested head does not contain its current base.")
    else:
        parents = repo.git("show", "-s", "--format=%P", tested).stdout.split()
        if len(parents) != 2 or set(parents) != {head, base}:
            raise ValueError("Test the PR head including its base, or an exact two-parent merge of those commits.")
    for ancestor in snapshot["ancestors"]:
        required = [ancestor["merged_sha"]] if ancestor["merged_sha"] else [ancestor["head_sha"], ancestor["base_sha"]]
        if not all(commit and repo.includes(tested, commit) for commit in required):
            raise ValueError(f"The tested commit does not include the effective revision of parent PR {ancestor['pr']}.")


def _test_status(row, snapshot):
    groups = {}
    for test in row["tests"]:
        if group := test.get("group"):
            key = ("group", group["plan_path"], group["target_path"], tuple(group["sections"]), tuple(group["command"]))
        else:
            key = ("check", test["check"])
        groups.setdefault(key, []).append(test)
    checks = []
    for key in sorted(groups):
        test = stages.latest(groups[key])
        changed = [field for field in snapshot if test["snapshot"].get(field) != snapshot[field]]
        status = "recheck" if changed else ("current" if test["outcome"] == "passed" else test["outcome"])
        checks.append({"status": status, "test": test, "changed": changed})
    statuses = {check["status"] for check in checks}
    summary = next((s for s in ("recheck", "failed", "timed_out", "skipped", "current") if s in statuses), "untested")
    return {"status": summary, "test": stages.latest(row["tests"]), "checks": checks,
            "changed": sorted({field for check in checks for field in check["changed"]})}


def _report(repo, prs, selected, environment, history):
    identities = cache(lambda key: _identity(repo, prs[key]))
    result = {}
    for key in ([selected] if selected else sorted(prs)):
        row = prs[key]
        snapshot = _snapshot(prs, key, identities)
        head = snapshot["head_sha"]
        environments = {e["environment"] for e in row["observations"] if e["environment"]}
        environments.update(row.get("required_acceptance", {}))
        result[key] = {
            **snapshot, "pr": key, "parent": row["parent"], "expected_base_sha": row["expected_base_sha"],
            "base_tip_sha": repo.ref(row["base_ref"]), "tree_sha": repo.tree(head) if head else None, "verification": _test_status(row, snapshot),
            "stages": stages.stage_report(row, snapshot, environment),
            "environments": sorted(environments),
        }
        if artifacts := row.get("artifacts"):
            release_sha = snapshot["merged_sha"] or head
            result[key]["artifacts"] = [
                {**receipt, "status": "current" if receipt["sha"] == release_sha else "stale"}
                for receipt in artifacts if environment is None or receipt["environment"] == environment
            ]
            result[key]["environments"] = sorted(set(result[key]["environments"]) | {r["environment"] for r in artifacts})
        if history:
            result[key].update(tests=row["tests"], observations=row["observations"])
    return {"success": True, "version": 1, "environment": environment, "prs": result}


def _change(repo, prs, args):
    key = args.pr
    if not team_state.is_plain_text(key, 200):
        raise ValueError("Use a repository-local PR identifier.")
    if args.delivery_action == "track":
        head, base = repo.ref(args.head_ref), repo.ref(args.base_ref)
        if not head or not base:
            raise ValueError("Both tracked refs must resolve to local commits.")
        if args.expected_base is not None and repo.commit(args.expected_base) != base:
            raise ValueError("The base ref differs from the supplied expected base.")
        row = prs.setdefault(key, {"tests": [], "observations": []})
        if row["observations"] and _identity(repo, row)["merged_sha"]:
            raise ValueError("This PR has already landed. Track new work under its own PR identifier.")
        row.update(head_ref=args.head_ref, base_ref=args.base_ref, parent=args.parent,
                   expected_base_sha=base)
        _parents(prs, key)
        return
    row = prs[key]
    if args.delivery_action == "require":
        stages.configure(row, args.stage, args.environment, args.name or [])
        return
    snapshot = _snapshot(prs, key, cache(lambda pr: _identity(repo, prs[pr])))
    receipt = _provenance(args)
    if args.delivery_action == "test":
        _record_test(repo, row, snapshot, receipt, args)
    else:
        _observe(repo, row, snapshot, receipt, args)


def _record_test(repo, row, snapshot, receipt, args):
    evidence = {"subject_source": "attestation", "check": args.check or "pr"}
    outcome = args.outcome or "passed"
    if args.group_report:
        from . import delivery_group

        if args.outcome is not None or args.check is not None:
            raise ValueError("The group report supplies its own check and outcome.")
        tested, outcome, group = delivery_group.read_test(args.group_report, repo, args.pr)
        if finished := group.get("finished_at"):
            if args.observed_at and receipt["observed_at"] != finished:
                raise ValueError("The supplied observation time differs from the recorded group finish time.")
            receipt["observed_at"] = finished
        # A replay is the same observation even after a restack or after its sibling checks rerun.
        prior = next((r for r in row["tests"] if r.get("group", {}).get("run_id") == group["run_id"]), None)
        if prior:
            if prior["group"] != group or prior["outcome"] != outcome or prior["tested_sha"] != tested:
                raise ValueError("This group run identifier is already associated with different evidence.")
            return
        evidence = {"subject_source": "group_report", "group": group}
    elif args.checkout:
        if repo.git("status", "--porcelain", "--untracked-files=all").stdout:
            raise ValueError("A checkout attestation requires a clean working tree and index.")
        tested = repo.git("rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
        evidence["subject_source"] = "checkout"
    else:
        tested = repo.commit(args.tested_sha)
    if snapshot["merged_sha"]:
        if tested != snapshot["merged_sha"]:
            raise ValueError("After merge, record the check for the exact landing commit.")
    else:
        _check_test_subject(repo, snapshot, tested)
    row["tests"].append({**receipt, **evidence, "tested_sha": tested, "tree_sha": repo.tree(tested),
                         "outcome": outcome, "snapshot": snapshot})
    row["expected_base_sha"] = snapshot["base_sha"]


def _observe(repo, row, snapshot, receipt, args):
    stage = args.stage
    if args.outcome not in stages.OUTCOMES[stage]:
        raise ValueError(f"Choose a {stage} outcome from {', '.join(sorted(stages.OUTCOMES[stage]))}.")
    env = args.environment.strip() if args.environment else None
    if (stage in stages.ENV_STAGES) != bool(env):
        raise ValueError("Supply an environment for deploy, live and acceptance only.")
    sha = repo.commit(args.sha)
    if (stage == "merge") != bool(args.head_sha):
        raise ValueError("Supply --head-sha for merge observations only.")
    event = {**receipt, "stage": stage, "outcome": args.outcome, "environment": env, "sha": sha}
    if args.name is not None:
        event["name"] = stages.observation_name(row, stage, env, args.name)
    if (stage == "ci") != bool(args.ref):
        raise ValueError("Supply the run's --ref for CI observations only.")
    branch_ci = stage == "ci" and args.ref == row["head_ref"]
    if stage == "ci":
        if args.ref not in (row["head_ref"], row["base_ref"]):
            raise ValueError("CI must name this PR's full head or target ref.")
        event["ref"] = args.ref
    if stage == "review" or branch_ci:
        if not args.base_sha:
            raise ValueError("Supply the exact --base-sha covered by review or PR CI.")
        base = repo.commit(args.base_sha)
        event.update(base_sha=base, basis=None)
        if base == snapshot["base_sha"] and (sha == snapshot["head_sha"] or branch_ci):
            try:
                _check_test_subject(repo, snapshot, sha)
            except ValueError:
                pass  # Retain prior or incomplete evidence, never bind it to this current stack.
            else:
                event["basis"] = stages.review_basis(snapshot)
    elif stage == "merge":
        source_head = repo.commit(args.head_sha)
        event["head_sha"] = source_head
        if args.outcome == "merged":
            target = repo.ref(row["base_ref"])
            if not target or not repo.includes(target, sha):
                raise ValueError(f"Cannot verify the landing on {row['base_ref']}. Refresh the tracked target ref and retry.")
            base = row["expected_base_sha"]
            if args.base_sha:
                base = repo.commit(args.base_sha)
            elif snapshot["merged_sha"] == sha and snapshot["head_sha"] == source_head:
                base = snapshot["base_sha"]
            elif sha != source_head:
                base = repo.git("rev-parse", "--verify", sha + "^1").stdout.strip()
            if not repo.includes(sha, base):
                raise ValueError("The pre-merge base must be an ancestor of the landing. Check --base-sha.")
            event["basis"] = {**stages.review_basis(snapshot), "head_sha": source_head, "base_sha": base}
    elif args.base_sha:
        raise ValueError("Use --base-sha only for review, PR CI or a merge.")
    row["observations"].append(event)


def _read(path):
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ValueError("The delivery ledger must be a regular, single-link file.")
    if not path.exists():
        return {}
    state = storage.load_json(path)
    if state.get("version") != 1 or not isinstance(state.get("prs"), dict):
        raise ValueError("Unsupported or invalid delivery ledger; preserve the existing file.")
    return state["prs"]


@contextmanager
def edit_ledger(path):
    """Serialize changes across linked worktrees and publish only a successful operation."""
    lock = path.with_suffix(".json.lock")
    if lock.is_symlink() or (lock.exists() and (not lock.is_file() or lock.stat().st_nlink != 1)):
        raise ValueError("The delivery lock must be a regular, single-link file.")
    with storage.file_lock(path):
        prs = _read(path)
        yield prs
        storage.write_json(path, {"version": 1, "prs": prs})


def run(args):
    try:
        repo = Repository(args.target_dir)
        environment = getattr(args, "environment", None)
        env = environment.strip() if environment is not None else None
        if args.delivery_action == "status":
            prs = _read(repo.ledger)
            result = _report(repo, prs, args.pr, env, args.history)
            if args.format == "markdown":
                from . import delivery_summary

                print(delivery_summary.markdown(result))
                return 0
        else:
            with edit_ledger(repo.ledger) as prs:
                _change(repo, prs, args)
                result = _report(repo, prs, args.pr, env, False)
    except KeyError as exc:
        result = {"success": False, "error": f"Unknown PR or missing ledger field: {exc}."}
    except (AttributeError, TypeError, OSError, ValueError) as exc:
        result = {"success": False, "error": str(exc)}
    return output.print_json(result, 0 if result["success"] else 1)
