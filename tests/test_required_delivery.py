"""Named requirements keep each CI job and environment sign-off independently accountable."""
from test_delivery import commit, git, observe, stack


def test_ci_reports_each_required_job_at_the_exact_basis(stack):
    repo, call, base, head, child, tip = stack
    call("require", "--pr", "1", "--stage", "ci", "--name", "server", "--name", "web", "--name", "migration")
    check = ("--ref", "refs/heads/parent", "--base-sha", base)

    def status():
        return call("status", "--pr", "1")["prs"]["1"]["stages"]["ci"]

    assert status()["outcome"] == "missing"
    observe(call, "1", "ci", "passed", head, *check)  # An aggregate run cannot substitute for a named job.
    observe(call, "1", "ci", "failed", head, *check, "--name", "server")
    observe(call, "1", "ci", "passed", head, *check, "--name", "web")
    stage = status()
    assert stage["outcome"] == "failed"
    assert {k: v["outcome"] for k, v in stage["requirements"].items()} == {
        "server": "failed", "web": "passed", "migration": "missing"}
    assert stage["requirements"]["server"]["source"] == "delivery-record/7"
    observe(call, "1", "ci", "passed", head, *check, "--name", "server")
    assert status()["outcome"] == "missing"
    observe(call, "1", "ci", "skipped", head, *check, "--name", "migration")
    assert status()["outcome"] == "skipped"
    observe(call, "1", "ci", "passed", head, *check, "--name", "migration")
    observe(call, "1", "ci", "failed", head, *check, "--name", "migration",
            "--observed-at", "2020-01-01T00:00:00Z")
    assert status()["outcome"] == "passed"

    git(repo, "checkout", "main")
    commit(repo, "base-advance")
    stale = status()
    assert stale["outcome"] == "stale"
    assert all(item["previous_outcome"] == "passed" for item in stale["requirements"].values())
    assert call("status", "--pr", "4")["prs"]["4"]["stages"]["ci"] == {"outcome": "unknown"}


def test_fast_forward_ci_needs_evidence_for_each_landed_job(stack):
    repo, call, base, head, child, tip = stack
    call("require", "--pr", "1", "--stage", "ci", "--name", "unit")
    observe(call, "1", "ci", "passed", head, "--name", "unit", "--ref", "refs/heads/parent", "--base-sha", base)
    git(repo, "checkout", "main")
    git(repo, "merge", "--ff-only", "parent")
    row = observe(call, "1", "merge", "merged", head, "--head-sha", head)["prs"]["1"]
    assert row["stages"]["ci"]["requirements"]["unit"]["outcome"] == "stale"
    updated = observe(call, "1", "ci", "passed", head, "--name", "unit", "--ref", "refs/heads/main")
    assert updated["prs"]["1"]["stages"]["ci"]["outcome"] == "passed"


def test_acceptance_is_independent_by_name_environment_and_revision(stack):
    repo, call, base, head, child, tip = stack
    for env in (" uat ", "production"):
        declaration = call("require", "--pr", "1", "--stage", "acceptance", "--environment", env, "--name", "Finance", "--name", "IAM")
        assert declaration["prs"]["1"]["stages"]["acceptance"]["outcome"] == "missing"

    def stage(env):
        return call("status", "--pr", "1", "--environment", env)["prs"]["1"]["stages"]["acceptance"]

    assert call("status", "--pr", "1")["prs"]["1"]["environments"] == ["production", "uat"]
    for env in ("uat", "production"):
        observe(call, "1", "acceptance", "accepted", head, "--environment", env, "--name", "Finance")
    observe(call, "1", "acceptance", "rejected", head, "--environment", "uat", "--name", "IAM")
    assert stage(" uat ")["outcome"] == "rejected"
    prod = stage("production")
    assert prod["outcome"] == "missing" and prod["requirements"]["IAM"] == {"outcome": "missing"}
    assert prod["requirements"]["Finance"]["actor"] == "reviewer"
    observe(call, "1", "acceptance", "accepted", head, "--environment", "production", "--name", "IAM")
    assert stage("production")["outcome"] == "accepted"
    git(repo, "checkout", "parent")
    commit(repo, "approval-invalidated")
    assert stage("production")["requirements"]["Finance"]["previous_outcome"] == "accepted"


def test_reconfiguration_preserves_history_and_invalid_requests_are_atomic(stack):
    repo, call, base, head, child, tip = stack
    flags = ("--pr", "1", "--stage", "ci")
    check = ("--ref", "refs/heads/parent", "--base-sha", base)
    legacy = observe(call, "1", "ci", "passed", head, *check)["prs"]["1"]["stages"]["ci"]
    call("require", *flags, "--name", "build")
    observe(call, "1", "ci", "failed", head, *check, "--name", "build")
    call("require", *flags, "--name", "build", "--name", "lint")
    before = call("status", "--pr", "1", "--history")
    assert before["prs"]["1"]["stages"]["ci"]["outcome"] == "failed"
    for args in (("--name", "new", "--name", "new"), ("--name", "bad\nname"), ("--name", "x", "--environment", "prod")):
        assert call("require", *flags, *args, ok=False)["error"]
    assert call("require", "--pr", "1", "--stage", "acceptance", "--name", "Finance", ok=False)["error"]
    assert observe(call, "1", "ci", "passed", head, *check, "--name", "typo", ok=False)["error"]
    assert observe(call, "1", "deploy", "passed", head, "--environment", "prod", "--name", "build", ok=False)["error"]
    assert call("status", "--pr", "1", "--history") == before
    call("require", *flags, "--name", "lint")
    assert call("status", "--pr", "1")["prs"]["1"]["stages"]["ci"] == {
        "outcome": "missing", "requirements": {"lint": {"outcome": "missing"}}}
    call("require", *flags, "--clear")
    after = call("status", "--pr", "1", "--history")["prs"]["1"]
    assert after["stages"]["ci"] == legacy
    assert after["observations"] == before["prs"]["1"]["observations"]
