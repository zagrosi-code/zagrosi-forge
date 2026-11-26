"""Exercise persistent delivery evidence through the public CLI against real local Git refs."""
import hashlib
import json
import subprocess

import pytest

from forge_test_helpers import load_zagrosi_module


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=True).stdout.strip()


def commit(repo, name):
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def stack(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "tester@example.org")
    git(repo, "config", "user.name", "Tester")
    sections = repo / "plan" / "sections"
    sections.mkdir(parents=True)
    for name in ("section-01-labels", "section-02-search"):
        (sections / (name + ".md")).write_text("## Owned files\n- feature.py\n")
    (sections / "index.md").write_text("<!-- SECTION_MANIFEST\nsection-01-labels\nsection-02-search\nEND_MANIFEST -->\n")
    (sections.parent / "codex-plan.md").write_text(
        '<!-- FORGE_META\n{"artifact_type":"compact_plan","depth_mode":"lean","source":"spec.md"}\nEND_FORGE_META -->\n')
    (sections.parent / "spec.md").write_text("REQ-001 Implement selected labels and search.\n")
    git(repo, "add", "plan")
    base = commit(repo, "base")
    git(repo, "branch", "stable")
    git(repo, "checkout", "-b", "parent")
    parent = commit(repo, "parent-feature")
    git(repo, "checkout", "-b", "child")
    child = commit(repo, "child-feature")
    git(repo, "checkout", "-b", "grandchild")
    tip = commit(repo, "last-feature")
    forge = load_zagrosi_module()

    def call(action, *args, target=None, ok=True):
        code = forge.entrypoint.main(["delivery", action, "--target-dir", str(target or repo), *args])
        result = json.loads(capsys.readouterr().out)
        assert bool(code == 0) == ok, result
        return result

    def track(pr, head, base_ref, *extra):
        return call("track", "--pr", pr, "--head-ref", "refs/heads/" + head,
                    "--base-ref", "refs/heads/" + base_ref, *extra)

    track("1", "parent", "main")
    track("2", "child", "parent", "--parent", "1")
    track("3", "grandchild", "child", "--parent", "2")
    track("4", "stable", "main")
    return repo, call, base, parent, child, tip


def record_test(call, pr, sha, **kwargs):
    return call("test", "--pr", pr, "--tested-sha", sha, "--source", "checks/run/22", "--actor", "ci", **kwargs)


def observe(call, pr, stage, outcome, sha, *extra):
    return call("observe", "--pr", pr, "--stage", stage, "--outcome", outcome, "--sha", sha,
                "--source", "delivery-record/7", "--actor", "reviewer", *extra)


def test_stack_drift_only_affects_related_descendants(stack):
    repo, call, base, parent, child, tip = stack
    for pr, sha in [("1", parent), ("2", child), ("3", tip), ("4", base)]:
        record_test(call, pr, sha)
    assert call("status")["prs"]["3"]["verification"]["status"] == "current"
    git(repo, "checkout", "parent")
    new_parent = commit(repo, "parent-fix")
    rows = call("status")["prs"]
    assert [rows[str(i)]["verification"]["status"] for i in (1, 2, 3, 4)] == ["recheck", "recheck", "recheck", "current"]
    assert rows["2"]["expected_base_sha"] == parent
    assert rows["2"]["base_sha"] == new_parent
    assert record_test(call, "2", child, ok=False)["error"]
    git(repo, "checkout", "-b", "ci-merge", "child")
    git(repo, "merge", "--no-ff", "parent", "-m", "tested merge")
    merged = git(repo, "rev-parse", "HEAD")
    record_test(call, "2", merged)
    row = call("status", "--pr", "2")["prs"]["2"]
    assert row["verification"]["status"] == "current"
    assert row["verification"]["test"]["tested_sha"] == merged
    assert row["verification"]["test"]["tree_sha"] == git(repo, "rev-parse", merged + "^{tree}")
    stray = commit(repo, "unrelated")
    assert record_test(call, "2", stray, ok=False)["error"]


def test_stage_evidence_is_scoped_by_sha_and_environment(stack):
    repo, call, base, parent, child, tip = stack
    observe(call, "1", "review", "approved", parent, "--base-sha", base)
    observe(call, "1", "ci", "passed", parent, "--ref", "refs/heads/parent", "--base-sha", base)
    observe(call, "1", "deploy", "skipped", parent, "--environment", "production")
    observe(call, "1", "live", "passed", parent, "--environment", "uat")
    rows = call("status", "--environment", "production")["prs"]
    stages = rows["1"]["stages"]
    assert {k: v["outcome"] for k, v in stages.items()} == {
        "review": "approved", "merge": "unknown", "ci": "passed", "deploy": "skipped",
        "live": "unknown", "acceptance": "unknown",
    }
    assert stages["deploy"]["source"] == "delivery-record/7"
    assert stages["deploy"]["actor"] == "reviewer"
    assert stages["deploy"]["observed_at"]
    assert stages["deploy"]["sha"] == parent
    assert stages["deploy"]["environment"] == "production"
    git(repo, "checkout", "parent")
    commit(repo, "fix")
    row = call("status", "--pr", "1", "--environment", "production")["prs"]["1"]
    assert row["stages"]["review"]["outcome"] == "stale"
    assert row["stages"]["ci"]["outcome"] == "stale"
    assert row["stages"]["acceptance"]["outcome"] == "unknown"


def test_squashed_parent_merge_and_deleted_branch_require_recheck(stack, tmp_path):
    repo, call, base, parent, child, tip = stack
    record_test(call, "2", child)
    git(repo, "checkout", "main")
    git(repo, "merge", "--squash", "parent")
    git(repo, "commit", "-m", "squash parent")
    landed = git(repo, "rev-parse", "HEAD")
    observe(call, "1", "merge", "merged", landed, "--head-sha", parent)
    observe(call, "1", "ci", "passed", parent, "--ref", "refs/heads/parent", "--base-sha", base)
    git(repo, "branch", "-D", "parent")
    second = tmp_path / "second"
    git(repo, "worktree", "add", "--detach", str(second), child)
    rows = call("status", target=second)["prs"]
    assert rows["1"]["stages"]["merge"]["sha"] == landed
    assert rows["1"]["stages"]["ci"]["outcome"] == "stale"
    assert rows["2"]["verification"]["status"] == "recheck"
    assert record_test(call, "2", child, ok=False)["error"]
    assert record_test(call, "1", parent, ok=False)["error"]
    assert record_test(call, "1", landed)["prs"]["1"]["verification"]["status"] == "current"
    observe(call, "1", "ci", "passed", landed, "--ref", "refs/heads/main")
    assert call("status", "--pr", "1")["prs"]["1"]["stages"]["ci"]["sha"] == landed


def test_invalid_inputs_leave_refs_and_existing_state_unchanged(stack):
    repo, call, base, parent, child, tip = stack
    before_refs = git(repo, "show-ref")
    before = call("status")
    assert call("track", "--pr", "1", "--head-ref", "refs/heads/parent", "--base-ref", "refs/heads/child",
                "--parent", "2", ok=False)["error"]
    assert call("observe", "--pr", "1", "--stage", "deploy", "--outcome", "passed", "--sha", parent,
                "--source", "job/4", "--actor", "ci", ok=False)["error"]
    assert before == call("status")
    assert before_refs == git(repo, "show-ref")


def test_review_replay_and_fast_forward_ci_stay_on_the_observed_ref(stack):
    repo, call, base, parent, child, tip = stack
    old = ("--base-sha", parent, "--observed-at", "2025-01-01T12:00:00Z")
    observe(call, "2", "review", "approved", child, *old)
    git(repo, "checkout", "parent")
    commit(repo, "new-upstream")
    observe(call, "2", "review", "approved", child, *old)
    assert call("status", "--pr", "2")["prs"]["2"]["stages"]["review"]["outcome"] == "stale"

    # The independent root PR's approval remains bound to the original review basis when it lands.
    observe(call, "4", "review", "approved", base, "--base-sha", base)
    observe(call, "4", "ci", "passed", base, "--ref", "refs/heads/stable", "--base-sha", base)
    observe(call, "4", "merge", "merged", base, "--head-sha", base)
    git(repo, "branch", "-f", "stable", child)
    row = call("status", "--pr", "4")["prs"]["4"]
    assert row["stages"]["review"]["outcome"] == "approved"
    assert row["stages"]["merge"]["outcome"] == "merged"
    assert row["stages"]["ci"]["outcome"] == "stale"
    observe(call, "4", "ci", "passed", base, "--ref", "refs/heads/main")
    assert call("status", "--pr", "4")["prs"]["4"]["stages"]["ci"]["outcome"] == "passed"


def test_checkout_attestation_requires_clean_matching_worktree(stack, tmp_path, monkeypatch):
    repo, call, base, parent, child, tip = stack
    alt = tmp_path / "other"
    git(repo, "worktree", "add", "--detach", str(alt), child)
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    args = ("--pr", "2", "--checkout", "--actor", "tester", "--source", "report.txt")
    record = call("test", *args, target=alt)["prs"]["2"]["verification"]["test"]
    assert record["subject_source"] == "checkout" and record["tested_sha"] == child
    (alt / "dirty").write_text("uncommitted")
    assert call("test", *args, target=alt, ok=False)["error"]


def group_report(repo, sha, name, run_id):
    files = {}
    for line in git(repo, "ls-tree", "-rl", sha, "--", "plan").splitlines():
        header, path = line.split("\t")
        mode, _, oid, size = header.split()
        files[path.removeprefix("plan/")] = {"mode": mode, "oid": oid, "size": int(size)}
    digest = hashlib.sha256(json.dumps(files, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    report = {"success": True, "handoff": {"schema": "forge-section-group-v1", "pull_request": "2",
            "commit_sha": sha, "tree_sha": git(repo, "rev-parse", sha + "^{tree}"), "plan_digest": digest,
            "plan_path": "plan", "target_path": ".", "sections": [name], "owned_paths": ["feature.py"]},
            "verification": {"run_id": run_id, "command": ["pytest", name], "returncode": 0,
                             "timed_out": False, "seconds": 1.2}}
    set_group_history(report)
    return report


def set_group_history(report):
    run = report["verification"]
    command = json.dumps(run["command"], separators=(",", ":"), sort_keys=True).encode()
    report["history"] = {"group_check": {"run_id": run["run_id"], "seconds": run["seconds"],
           "returncode": run["returncode"], "passed": report["success"], "command_sha256": hashlib.sha256(command).hexdigest()}}


def test_import_group_report_exposes_independent_checks_and_blocks_old_run_replay(stack, tmp_path):
    repo, call, base, parent, child, tip = stack
    reports = [group_report(repo, child, name, char * 32) for name, char in
               [("section-01-labels", "a"), ("section-02-search", "b")]]
    file = tmp_path / "report.json"
    args = ("--pr", "2", "--group-report", str(file), "--source", "handoff/17", "--actor", "ci")
    for report in reports:
        file.write_text(json.dumps(report))
        row = call("test", *args)["prs"]["2"]["verification"]
        assert row["test"]["subject_source"] == "group_report"
    alt = tmp_path / "next"
    git(repo, "worktree", "add", "--detach", str(alt), child)
    checks = call("status", "--pr", "2", target=alt)["prs"]["2"]["verification"]["checks"]
    assert {c["test"]["group"]["sections"][0] for c in checks} == {"section-01-labels", "section-02-search"}
    assert all(c["test"]["tested_sha"] == child and c["status"] == "current" for c in checks)
    # Relabeling the same base commit changes the intended PR target, even if bytes match.
    git(repo, "branch", "replacement", parent)
    call("track", "--pr", "2", "--head-ref", "refs/heads/child", "--base-ref", "refs/heads/replacement", "--parent", "1")
    file.write_text(json.dumps(reports[0]))
    check = call("test", *args)["prs"]["2"]["verification"]
    assert check["status"] == "recheck" and all(c["status"] == "recheck" for c in check["checks"])
    reports[0]["verification"]["run_id"] = "c" * 32
    set_group_history(reports[0])
    file.write_text(json.dumps(reports[0]))
    check = call("test", *args)["prs"]["2"]["verification"]
    assert sorted(c["status"] for c in check["checks"]) == ["current", "recheck"]


def test_import_group_rejects_mismatches_and_preserves_timeout(stack, tmp_path):
    repo, call, base, parent, child, tip = stack
    file = tmp_path / "report.json"
    args = ("--pr", "2", "--group-report", str(file), "--source", "checks/7", "--actor", "ci")
    for field, value in [("pull_request", "1"), ("tree_sha", "0" * 40), ("plan_digest", "0" * 64), ("sections", ["section-99-missing"])]:
        data = group_report(repo, child, "section-01-labels", "a" * 32)
        data["handoff"][field] = value
        file.write_text(json.dumps(data))
        assert call("test", *args, ok=False)["error"]
    data = group_report(repo, child, "section-01-labels", "a" * 32)
    data["verification"].update(returncode=124, timed_out=True)
    file.write_text(json.dumps(data))
    assert call("test", *args, ok=False)["error"]
    data["success"] = False
    set_group_history(data)
    file.write_text(json.dumps(data))
    row = call("test", *args)["prs"]["2"]["verification"]
    assert row["status"] == "timed_out" and row["test"]["group"]["returncode"] == 124


def test_group_dirty_snapshot_downgrades_pass_and_works_with_qualified_pr_id(stack, tmp_path):
    repo, call, base, parent, child, tip = stack
    pr = "example/app#42"
    call("track", "--pr", pr, "--head-ref", "refs/heads/child", "--base-ref", "refs/heads/parent")
    data = group_report(repo, child, "section-01-labels", "a" * 32)
    data["handoff"]["pull_request"] = pr
    file = tmp_path / "report.json"
    args = ("--pr", pr, "--group-report", str(file), "--source", "checks/7", "--actor", "ci")
    file.write_text(json.dumps(data))
    assert call("test", *args)["prs"][pr]["verification"]["status"] == "current"
    data["verification"].update(run_id="b" * 32, snapshot_error="The checkout changed.")
    data["success"] = False
    set_group_history(data)
    file.write_text(json.dumps(data))
    verify = call("test", *args)["prs"][pr]["verification"]
    assert verify["status"] == "failed" and verify["test"]["group"]["snapshot_error"]


def test_late_group_import_uses_observed_finish_time(stack, tmp_path):
    repo, call, base, parent, child, tip = stack
    file = tmp_path / "report.json"
    args = ("--pr", "2", "--group-report", str(file), "--source", "ci/archived", "--actor", "ci")
    for run, day, code in [("a", "02", 0), ("b", "01", 1)]:
        data = group_report(repo, child, "section-01-labels", run * 32)
        data["verification"].update(started_at=f"2026-10-{day}T10:00:00Z", finished_at=f"2026-10-{day}T10:00:01Z", returncode=code)
        data["success"] = code == 0
        set_group_history(data)
        file.write_text(json.dumps(data))
        row = call("test", *args)["prs"]["2"]["verification"]
    assert row["status"] == "current" and row["test"]["group"]["run_id"] == "a" * 32
    assert row["test"]["observed_at"].startswith("2026-10-02T10:00:01")
    assert call("test", *args, "--observed-at", "2026-11-01T01:02:03Z", ok=False)["error"]


def test_non_ascii_checkout_uses_the_real_git_common_directory(stack, tmp_path, monkeypatch):
    repo, call, *_ = stack
    moved = tmp_path / 'café µ'
    repo.rename(moved)
    # Git emits UTF-8 paths even when Windows defaults redirected text to cp1252.
    run = subprocess.run
    def windows_pipe_encoding(*args, **kwargs):
        if kwargs.get('text'):
            kwargs.setdefault('encoding', 'cp1252')
        return run(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', windows_pipe_encoding)
    call('track', '--pr', 'unicode-path', '--head-ref', 'refs/heads/child',
         '--base-ref', 'refs/heads/main', target=moved)
    ledger = json.loads((moved / '.git' / 'forge-delivery.json').read_text(encoding='utf-8'))
    assert 'unicode-path' in ledger['prs']
