"""The published walkthrough is executable with only Python and Git."""
import hashlib
import json
import os
import subprocess
import sys

from forge_test_helpers import ROOT


def test_walkthrough_reproduces_handoff_and_recovery(tmp_path):
    script = ROOT / "examples" / "release-handoff" / "run.py"
    out = tmp_path / "review"
    command = [sys.executable, str(script), "--keep", str(out)]
    other = tmp_path / "unrelated-project"
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "Original"], check=True)
    (other / "invoice").write_text("existing user data", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "add", "invoice"], check=True)
    subprocess.run(["git", "-C", str(other), "-c", "user.email=example@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "original"], check=True)
    gitdir = other / ".git"
    protected = [gitdir / name for name in ("config", "index", "HEAD", "refs/heads/main")]
    before = [path.read_bytes() for path in protected]
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "GIT_DIR": str(gitdir), "GIT_WORK_TREE": str(other),
           "GIT_INDEX_FILE": str(gitdir / "index"), "GIT_COMMON_DIR": str(gitdir)}
    run = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=120)
    assert before == [path.read_bytes() for path in protected]
    assert subprocess.check_output(["git", "-C", str(other), "branch", "--format=%(refname:short)"],
                                   text=True).splitlines() == ["main"]
    assert run.returncode == 0, run.stdout + run.stderr
    initial_summary = (out / "initial.md").read_text(encoding="utf-8")
    assert initial_summary in run.stdout
    assert "  - `schema` — passed" in initial_summary.splitlines()
    assert "  - `migration` — missing" in initial_summary.splitlines()
    stale_summary = (out / "stale.md").read_text(encoding="utf-8")
    assert "recheck" in stale_summary
    parent = subprocess.check_output(["git", "-C", str(out / "author"), "rev-parse", "refs/heads/invoice-schema"],
                                     text=True).strip()
    actions = stale_summary.split("### Next action")[1].splitlines()
    assert any("refs/heads/invoice-schema" in line and parent in line for line in actions)

    initial = json.loads((out / "initial-receipt.json").read_text(encoding="utf-8"))
    final = json.loads((out / "final-receipt.json").read_text(encoding="utf-8"))
    for filename, section in [("initial-receipt", "section-01-schema"), ("rebased-schema-receipt", "section-01-schema"),
                              ("migration-receipt", "section-02-migration"), ("final-receipt", "section-02-migration")]:
        receipt = json.loads((out / f"{filename}.json").read_text(encoding="utf-8"))
        assert receipt["handoff"]["sections"] == [section]
    initial_sha = initial["handoff"]["commit_sha"]
    final_sha = final["handoff"]["commit_sha"]
    assert initial_sha != final_sha
    assert all(initial_sha not in line for line in actions)
    assert "stdout" not in final["verification"] and "stderr" not in final["verification"]
    reviewed = (out / "review.md").read_text(encoding="utf-8")
    assert "  - `schema` — passed" in reviewed.splitlines()
    assert "  - `migration` — passed" in reviewed.splitlines()
    artifacts = reviewed.split("### Artifacts")[1].split("### Next action")[0]
    rows = [line for line in artifacts.splitlines() if line.startswith("- ")]
    assert len(rows) == 2
    for directory, sha, status in [("initial-build", initial_sha, "stale"), ("rebased-build", final_sha, "current")]:
        build = out / directory
        receipt = json.loads((build / "artifact.json").read_text(encoding="utf-8"))["artifact"]
        assert receipt["sha"] == sha
        filename = "invoice-migration.zip"
        original = (build / filename).read_bytes()
        assert (build / "received" / filename).read_bytes() == original
        assert (build / "altered" / filename).read_bytes() != original
        digest = "sha256:" + hashlib.sha256(original).hexdigest()
        assert receipt["digest"] == digest
        row, = [line for line in rows if sha in line]
        assert f"`{filename}`" in row and f"`{digest}`" in row
        assert f"`build` in `review`, {status} at `{sha}`" in row
    assert "Altered ZIP rejected" in run.stdout
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode != 0
    assert (out / "review.md").read_text(encoding="utf-8") == reviewed
