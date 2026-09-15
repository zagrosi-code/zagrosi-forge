"""Forge diffs."""

from __future__ import annotations

from pathlib import Path
import argparse

from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import ownership as _ownership
from . import quality as _quality
from . import sections as _sections
from . import storage as _storage

def changed_files_from_diff(text: str) -> list[str]:
    lines = text.splitlines()
    is_unified_diff = any(
        line.startswith(("diff --git ", "--- ", "+++ ", "@@ "))
        for line in lines
    )
    if not is_unified_diff:
        return sorted({
            path
            for line in lines
            if (path := line.strip().removeprefix("./")) and path != "/dev/null"
        })

    files: set[str] = set()
    for line in lines:
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.add(parts[3].removeprefix("b/"))
        elif line.startswith("+++ b/"):
            files.add(line[6:].strip())
    return sorted(file for file in files if file != "/dev/null")


def git_changed_files(repo: Path, staged: bool) -> tuple[list[str], str | None]:
    commands = [["diff", "--name-only", "--cached"]]
    if not staged:
        commands = [
            ["diff", "--name-only"],
            ["diff", "--name-only", "--cached"],
            ["ls-files", "--others", "--exclude-standard"],
        ]
    changed: set[str] = set()
    for command in commands:
        result = _storage.git(command, repo)
        if result.returncode != 0:
            return [], result.stderr.strip() or result.stdout.strip()
        changed.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(changed), None


def patch_scope(args: argparse.Namespace) -> int:
    section_file = _storage.resolve_path(args.section_file)
    if not section_file.exists():
        return _output.print_json({"success": False, "error": f"Section file not found: {section_file}"}, 1)
    declared = set(_ownership.extract_section_owned_paths(_storage.read_text(section_file)))
    if args.diff_file:
        diff_path = _storage.resolve_path(args.diff_file)
        if not diff_path.exists():
            return _output.print_json({"success": False, "error": f"Diff file not found: {diff_path}"}, 1)
        changed = set(changed_files_from_diff(_storage.read_text(diff_path)))
    else:
        changed_list, error = git_changed_files(_storage.resolve_path(args.repo), args.staged)
        if error:
            return _output.print_json({"success": False, "error": error}, 1)
        changed = set(changed_list)

    out_of_scope = sorted(file for file in changed if file not in declared)
    missing_declared = sorted(file for file in declared if file not in changed)
    findings: list[_models.Finding] = []
    for file in out_of_scope:
        findings.append(_quality.finding("high", "out-of-scope-file", f"Changed file is not declared in section: {file}", section_file))
    if missing_declared:
        findings.append(
            _quality.finding(
                "low",
                "declared-file-not-changed",
                f"Declared files not present in patch: {', '.join(missing_declared)}",
                section_file,
            )
        )
    payload = _quality.quality_from_args(
        "patch-scope",
        findings,
        args,
        {
            "section_file": str(section_file),
            "declared_files": sorted(declared),
            "changed_files": sorted(changed),
            "out_of_scope": out_of_scope,
            "missing_declared": missing_declared,
        },
    )
    return _quality.emit_payload(payload, args)


def commit_message(args: argparse.Namespace) -> int:
    section_file = _storage.resolve_path(args.section_file)
    if not section_file.exists():
        return _output.print_json({"success": False, "error": f"Section file not found: {section_file}"}, 1)
    section = section_file.stem
    label = section.removeprefix("section-").replace("-", " ")
    if args.style == "conventional":
        subject = f"feat: implement {label}"
    else:
        subject = f"Implement {label}"
    text = _storage.read_text(section_file)
    req_ids = _markdown.requirement_ids(text)
    files = _ownership.extract_section_owned_paths(text)
    body_lines = []
    if req_ids:
        body_lines.append(f"Requirements: {', '.join(req_ids)}")
    if files:
        body_lines.append(f"Scope: {', '.join(files[:8])}" + (" ..." if len(files) > 8 else ""))
    body_lines.append("Tests and review follow the section plan.")
    return _output.print_json(
        {
            "success": True,
            "section_file": str(section_file),
            "subject": subject[:72],
            "body": "\n".join(body_lines),
            "message": subject[:72] + "\n\n" + "\n".join(body_lines),
        }
    )


def implementation_drift(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    findings: list[_models.Finding] = []
    if progress["state"] in {"invalid_index", "no_index"}:
        findings.append(_quality.finding("critical", "invalid-sections", "Drift detection requires valid sections.", planning_dir / "sections" / "index.md"))
        return _quality.emit_quality("implementation-drift", findings, args, {"section_progress": progress})
    if progress["state"] != "complete":
        findings.append(
            _quality.finding(
                "critical",
                "invalid-sections",
                "Drift detection requires every manifested section to exist and be non-empty.",
                planning_dir / "sections" / "index.md",
            )
        )
        return _quality.emit_quality("implementation-drift", findings, args, {"section_progress": progress})
    if args.diff_file:
        diff_path = _storage.resolve_path(args.diff_file)
        changed = set(changed_files_from_diff(_storage.read_text(diff_path)))
    else:
        changed_list, error = git_changed_files(_storage.resolve_path(args.repo), args.staged)
        if error:
            return _output.print_json({"success": False, "error": error}, 1)
        changed = set(changed_list)

    planned_files: set[str] = set()
    section_owned_files: dict[str, set[str]] = {}
    latest_owner: dict[str, str] = {}
    for section in progress["sections"]:
        section_path = planning_dir / "sections" / f"{section}.md"
        files = set(_ownership.extract_section_owned_paths(_storage.read_text(section_path)))
        section_owned_files[section] = files
        planned_files.update(files)
        for file in files:
            latest_owner[file] = section
    active_sections = {
        latest_owner[file]
        for file in changed
        if file in latest_owner
    }
    section_planned_tests = {
        section: {
            file
            for file in section_owned_files[section]
            if latest_owner.get(file) == section and _markdown.is_test_path(file)
        }
        for section in active_sections
    }
    planned_tests = {
        file
        for tests in section_planned_tests.values()
        for file in tests
    }
    changed_tests = changed.intersection(planned_tests)
    sections_missing_changed_tests = sorted(
        section
        for section, tests in section_planned_tests.items()
        if tests and not changed.intersection(tests)
    )
    out_of_scope = sorted(file for file in changed if file not in planned_files)
    missing_planned_tests = sorted(file for file in planned_tests if file not in changed_tests)
    for file in out_of_scope:
        findings.append(_quality.finding("high", "implementation-drift-file", f"Changed file was not planned: {file}", planning_dir))
    if sections_missing_changed_tests:
        findings.append(
            _quality.finding(
                "medium",
                "planned-tests-not-changed",
                "No changed test files match planned test ownership for active sections: "
                + ", ".join(sections_missing_changed_tests)
                + ".",
                planning_dir,
            )
        )
    return _quality.emit_quality(
        "implementation-drift",
        findings,
        args,
        {
            "planning_dir": str(planning_dir),
            "planned_files": sorted(planned_files),
            "changed_files": sorted(changed),
            "out_of_scope": out_of_scope,
            "active_sections": sorted(active_sections),
            "planned_tests": sorted(planned_tests),
            "changed_tests": sorted(changed_tests),
            "missing_planned_tests": missing_planned_tests,
            "sections_missing_changed_tests": sections_missing_changed_tests,
        },
    )
