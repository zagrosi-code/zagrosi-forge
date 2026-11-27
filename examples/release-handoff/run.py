"""Run the Forge handoff example with Python and Git. All changes stay in the demo directory."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
LAUNCHER = HERE.parents[1] / "scripts" / "zagrosi_skills.py"
PR = "42"
CHECK_SECTIONS = {"schema": "section-01-schema", "migration": "section-02-migration"}


def invoke(cwd, *command, expected=0):
    # Hooks, an inherited index or GIT_DIR from a parent build must not redirect a demo operation.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, encoding="utf-8")
    if result.returncode != expected:
        raise RuntimeError(f"{command!r} exited {result.returncode}\n{result.stdout}{result.stderr}")
    return result.stdout


def git(repo, *args):
    return invoke(repo, "git", *args).strip()


def forge(repo, *args, expected=0):
    result = json.loads(invoke(repo, sys.executable, "-X", "utf8", LAUNCHER, *args, expected=expected))
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def save(path, document):
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


def prepare_stack(directory):
    repo = directory / "author"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    for name, value in [("user.name", "Forge example"), ("user.email", "example@example.invalid"),
                        ("commit.gpgsign", "false"), ("core.hooksPath", str(directory / "empty-hooks"))]:
        git(repo, "config", name, value)
    shutil.copytree(HERE / "fixture" / "plan", repo / "plan")
    git(repo, "add", "plan")
    git(repo, "commit", "-m", "Review the migration plan")
    git(repo, "switch", "-c", "invoice-schema")
    shutil.copyfile(HERE / "fixture/schema.sql", repo / "schema.sql")
    git(repo, "add", "schema.sql")
    git(repo, "commit", "-m", "Create the invoice table")
    git(repo, "switch", "-c", "approval-migration")
    for name in ("migration.sql", "check.py"):
        shutil.copyfile(HERE / "fixture" / name, repo / name)
    git(repo, "add", "migration.sql", "check.py")
    git(repo, "commit", "-m", "Add the approval column")
    forge(repo, "delivery", "track", "--pr", "41", "--head-ref", "refs/heads/invoice-schema",
          "--base-ref", "refs/heads/main")
    forge(repo, "delivery", "track", "--pr", PR, "--head-ref", "refs/heads/approval-migration",
          "--base-ref", "refs/heads/invoice-schema", "--parent", "41")
    forge(repo, "delivery", "require", "--pr", PR, "--stage", "ci", "--name", "schema", "--name", "migration")
    receiver = directory / "reviewer"
    git(repo, "worktree", "add", "--detach", str(receiver), "approval-migration")
    return repo, receiver


def verify_check(author, reviewer, directory, name, filename):
    identity = forge(author, "section-group", "create", "--planning-dir", author / "plan",
                     "--sections", CHECK_SECTIONS[name], "--pr", PR)
    handoff = save(directory / "handoff.json", identity)
    report = forge(author, "section-group", "verify", "--handoff", handoff, "--omit-output",
                   "--", sys.executable, "check.py", name)
    receipt = save(directory / filename, report)
    source = "local-run/" + report["verification"]["run_id"]
    forge(reviewer, "delivery", "test", "--pr", PR, "--group-report", receipt,
          "--source", source, "--actor", "example-reviewer")
    forge(reviewer, "delivery", "observe", "--pr", PR, "--stage", "ci", "--name", name, "--outcome", "passed",
          "--sha", identity["handoff"]["commit_sha"], "--ref", "refs/heads/approval-migration",
          "--base-sha", git(author, "rev-parse", "invoice-schema"), "--source", source, "--actor", "example-reviewer")
    return identity["handoff"]["commit_sha"]


def verify_package(author, reviewer, directory, head):
    directory.mkdir()
    package = directory / "invoice-migration.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr(ZipInfo("migration.sql"), (author / "migration.sql").read_bytes())
    record = forge(author, "delivery", "artifact", "record", "--pr", PR, "--sha", head, "--stage", "build",
                   "--environment", "review", "--name", package.name, "--file", package,
                   "--actor", "example-builder", "--source", "demo/package")
    receipt = save(directory / "artifact.json", record)
    check = ["delivery", "artifact", "verify", "--receipt", receipt, "--file"]
    received = directory / "received" / package.name
    received.parent.mkdir()
    shutil.copyfile(package, received)
    if not forge(reviewer, *check, received)["matched"]:
        raise RuntimeError("The original ZIP did not verify.")
    altered = directory / "altered" / package.name
    altered.parent.mkdir()
    with ZipFile(altered, "w") as archive:
        archive.writestr(ZipInfo("migration.sql"), b"SELECT 1;\n")
    if forge(reviewer, *check, altered, expected=1).get("matched") is not False:
        raise RuntimeError("The verifier did not report a mismatch for the altered ZIP.")
    print("Original ZIP verified. Altered ZIP rejected.")


def summary(repo, destination):
    report = invoke(repo, sys.executable, "-X", "utf8", LAUNCHER, "delivery", "status", "--pr", PR,
                    "--environment", "review", "--format", "markdown")
    destination.write_text(report, encoding="utf-8")
    return report


def demonstrate(directory):
    repo, reviewer = prepare_stack(directory)
    head = verify_check(repo, reviewer, directory, "schema", "initial-receipt.json")
    verify_package(repo, reviewer, directory / "initial-build", head)
    print(summary(reviewer, directory / "initial.md"))
    verify_check(repo, reviewer, directory, "migration", "migration-receipt.json")

    git(repo, "switch", "invoice-schema")
    with (repo / "schema.sql").open("a", encoding="utf-8") as schema:
        schema.write("CREATE INDEX invoices_by_amount ON invoices(cents);\n")
    git(repo, "commit", "-am", "Index invoices by amount")
    print(summary(reviewer, directory / "stale.md"))
    git(repo, "switch", "approval-migration")
    git(repo, "rebase", "invoice-schema")
    git(reviewer, "checkout", "--detach", "approval-migration")
    verify_check(repo, reviewer, directory, "schema", "rebased-schema-receipt.json")
    new_head = verify_check(repo, reviewer, directory, "migration", "final-receipt.json")
    verify_package(repo, reviewer, directory / "rebased-build", new_head)
    print(summary(reviewer, directory / "review.md"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", type=Path, help="Create this new directory and retain the repos, ZIP and receipts.")
    args = parser.parse_args()
    if args.keep:
        directory = args.keep.resolve()
        if directory.exists():
            parser.error("--keep requires a new directory.")
        directory.mkdir(parents=True)
        demonstrate(directory)
        print(f"Review files saved in {directory}")
    else:
        with tempfile.TemporaryDirectory(prefix="forge-handoff-") as tmp:
            demonstrate(Path(tmp).resolve())


if __name__ == "__main__":
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    main()
