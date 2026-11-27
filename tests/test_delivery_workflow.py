"""A receiving engineer can trace a partial plan and see a later parent correction."""
import json
import subprocess
import sys

from forge_test_helpers import SCRIPT
from test_compact_plan import SECTION
from test_team_git import git
from test_team_plans import commit_plan


def forge(repo, *args):
    result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=repo,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_handoff_and_stale_parent_through_installed_entrypoints(tmp_path):
    origin = tmp_path / "work"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "config", "user.email", "test@example.invalid")
    git(origin, "config", "user.name", "Engineer")
    plan = commit_plan(origin)
    shared = origin / "docs" / "product-decisions.md"
    shared.parent.mkdir()
    shared.write_text("# Labels\n\n## Display\nKeep internal spaces and case.\n", encoding="utf-8")
    contract = plan / "codex-plan.md"
    contract.write_text(contract.read_text().replace('"source": "spec.md"', '"source": "spec.md", '
                        '"shared_documents": ["docs/product-decisions.md"]'), encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "bind a shared stakeholder decision")
    git(origin, "checkout", "-b", "upstream")
    git(origin, "commit", "--allow-empty", "-m", "upstream feature")
    git(origin, "checkout", "-b", "downstream")
    remaining = "section-02-export"
    index = plan / "sections/index.md"
    index.write_text(index.read_text().replace("END_MANIFEST", remaining + "\nEND_MANIFEST"))
    section = plan / "sections" / (SECTION + ".md")
    (section.parent / (remaining + ".md")).write_text(section.read_text().replace("labels", "export"))
    section.write_text(section.read_text() + "\nThe [display decision](../../../../docs/product-decisions.md#display) applies.\n")
    (origin / ".gitignore").write_text("__pycache__/\n")
    (origin / "src").mkdir()
    (origin / "src" / "labels.py").write_text("def normalize(value):\n    return value.strip()\n")
    (origin / "check_labels.py").write_text(
        'from src.labels import normalize\nassert normalize(" Ada  Lovelace ") == "Ada  Lovelace"\n')
    git(origin, "add", ".")
    git(origin, "commit", "-m", "deliver the first group")
    head = git(origin, "rev-parse", "HEAD").stdout.strip()
    for pr, branch, base, extra in [("1", "upstream", "main", []),
                                    ("2", "downstream", "upstream", ["--parent", "1"])]:
        forge(origin, "delivery", "track", "--pr", pr, "--head-ref", "refs/heads/" + branch,
              "--base-ref", "refs/heads/" + base, *extra)
    handoff = tmp_path / "handoff.json"
    initial = forge(origin, "section-group", "create", "--planning-dir", plan, "--sections", SECTION, "--pr", "2")
    handoff.write_text(json.dumps(initial))
    receiving = tmp_path / "receiving"
    git(origin, "worktree", "add", "--detach", str(receiving), head)
    inspected = forge(receiving, "section-group", "inspect", "--handoff", handoff)
    assert inspected["handoff"]["sections"] == [SECTION]
    assert inspected["handoff"]["commit_sha"] == head
    checked = forge(receiving, "section-group", "verify", "--handoff", handoff, "--", sys.executable, "check_labels.py")
    handoff.write_text(json.dumps(checked))
    forge(receiving, "delivery", "test", "--pr", "2", "--group-report", handoff,
          "--source", "handoff/evidence", "--actor", "receiving-engineer")
    measurements = forge(receiving, "build-report", "--planning-dir", receiving / plan.relative_to(origin),
                         "--group", handoff)
    assert measurements["pull_requests"]["2"]["observed_checks"] == 1
    assert measurements["groups"][0]["verification"]["check_wall_span_seconds"] is not None
    current = forge(origin, "delivery", "status", "--pr", "2")["prs"]["2"]
    assert current["verification"]["status"] == "current"
    assert current["verification"]["test"]["tested_sha"] == head
    git(origin, "checkout", "upstream")
    git(origin, "commit", "--allow-empty", "-m", "correct upstream")
    stale = forge(receiving, "delivery", "status", "--pr", "2")["prs"]["2"]
    assert stale["verification"]["status"] == "recheck"
    assert stale["expected_base_sha"] != stale["base_sha"]
    assert stale["verification"]["test"]["tested_sha"] == head
    [needed] = stale["verification"]["checks"]
    assert needed["status"] == "recheck"
    assert needed["test"]["group"]["sections"] == [SECTION]
    rerun = needed["test"]["group"]["command"]
    assert rerun == [sys.executable, "check_labels.py"]

    git(origin, "checkout", "downstream")
    git(origin, "rebase", "upstream")
    restacked = git(origin, "rev-parse", "HEAD").stdout.strip()
    assert restacked != head
    git(receiving, "checkout", "--detach", restacked)
    updated = forge(origin, "section-group", "create", "--planning-dir", plan, "--sections", SECTION, "--pr", "2")
    handoff.write_text(json.dumps(updated))
    repeated = forge(receiving, "section-group", "verify", "--handoff", handoff, "--", *rerun)
    handoff.write_text(json.dumps(repeated))
    forge(receiving, "delivery", "test", "--pr", "2", "--group-report", handoff,
          "--source", "handoff/retested", "--actor", "receiving-engineer")
    resolved = forge(origin, "delivery", "status", "--pr", "2", "--history")["prs"]["2"]
    assert resolved["verification"]["status"] == "current"
    assert [record["tested_sha"] for record in resolved["tests"]] == [head, restacked]
