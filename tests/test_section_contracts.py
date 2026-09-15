from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forge_test_helpers import (
    ROOT,
    run_cmd,
    run_raw,
    write_required_plan_artifacts,
)


def test_lint_project_manifest_fixture() -> None:
    for example in ("saas", "typescript-app"):
        result = run_cmd("lint-project-manifest", "--planning-dir", str(ROOT / "examples" / example), "--strict")
        assert result["success"] is True
        assert result["score"] == 100


def test_lint_sections_rejects_shell_gates_for_non_predecessor_owned_paths(tmp_path: Path) -> None:
    planning = tmp_path / "planning"
    sections = planning / "sections"
    sections.mkdir(parents=True)
    manifest = [
        "section-01-root",
        "section-02-direct",
        "section-03-current",
        "section-04-successor",
        "section-05-parallel",
    ]
    (sections / "index.md").write_text(
        "<!-- PROJECT_CONFIG\n"
        "runtime: python-uv\n"
        "test_command: uv run pytest\n"
        "END_PROJECT_CONFIG -->\n\n"
        "<!-- SECTION_MANIFEST\n"
        + "\n".join(manifest)
        + "\nEND_MANIFEST -->\n\n"
        "# Sections\n\n"
        "## Dependency Graph\n\n"
        "| Section | Depends On | Blocks | Parallelizable |\n"
        "|---|---|---|---|\n"
        "| section-01-root | none | section-02-direct, section-05-parallel | Yes |\n"
        "| section-02-direct | section-01-root | section-03-current | No |\n"
        "| section-03-current | section-02-direct | section-04-successor | No |\n"
        "| section-04-successor | section-03-current | none | No |\n"
        "| section-05-parallel | section-01-root | none | Yes |\n\n"
        "## Execution Order\n\n"
        "Run the root, direct, current, and successor sections in dependency sequence. The parallel section may run "
        "concurrently after the root. "
        + "The dependency graph, execution order, blocking edge, and parallel boundary are explicit. " * 20
    )
    owned = {
        "section-01-root": ["tests/root.py"],
        "section-02-direct": ["tests/direct.py"],
        "section-03-current": ["tests/current.py"],
        "section-04-successor": [
            "tests/continued.py",
            "tests/quoted.py",
            "tests/successor.py",
        ],
        "section-05-parallel": ["tests/parallel.py"],
    }

    def section_text(section: str, gates: str = "") -> str:
        return (
            f"# {section}\n\n"
            "## Goal and dependencies\n\n"
            "Implement the section after its declared dependencies. Non-goals exclude every successor and parallel "
            "implementation boundary.\n\n"
            "## Exact path ownership\n\n"
            "This section owns exactly these paths:\n\n"
            "```text\n"
            + "\n".join(owned[section])
            + "\n```\n\n"
            "## Tests First\n\n"
            "Write the expected failure first, then implement the contract and run verification. The test matrix covers "
            "success, rejection, replay, rollback, security, privacy, and acceptance.\n\n"
            "## Implementation and acceptance\n\n"
            "The current state, architecture rationale, interface contract, file tree, phase plan, risks, rollout, and "
            "acceptance criteria are fixed for this implementation. "
            + "Tests first establish the expected failure before implementation, then verification proves the contract. " * 22
            + gates
        )

    allowed_and_near_miss_gates = (
        "\n\n## Shell gates and near misses\n\n"
        "```bash\n"
        "pytest tests/current.py\n"
        "pytest \"tests/direct.py\"\n"
        "pytest \\\n"
        "  tests/root.py::test_transitive\n"
        "pytest tests/unowned-baseline.py\n"
        "pytest prefix/tests/successor.py\n"
        "pytest tests/successor.py.suffix\n"
        "pytest tests/successor.pyextra\n"
        "```\n\n"
        "Prose-only example: pytest tests/successor.py must not be treated as a shell gate.\n\n"
        "```python\n"
        "run('pytest tests/successor.py')\n"
        "```\n\n"
        "```console\n"
        "pytest tests/successor.py\n"
        "```\n"
    )
    for section in manifest:
        gates = allowed_and_near_miss_gates if section == "section-03-current" else ""
        (sections / f"{section}.md").write_text(section_text(section, gates))
    write_required_plan_artifacts(planning)

    def lint_findings() -> tuple[subprocess.CompletedProcess[str], list[dict]]:
        result = run_raw("lint-sections", "--planning-dir", str(planning), "--depth", "fast")
        payload = json.loads(result.stdout)
        return result, payload["findings"]

    def gate_findings() -> tuple[subprocess.CompletedProcess[str], list[dict]]:
        result, findings = lint_findings()
        return result, [
            item
            for item in findings
            if item["code"] == "section-gate-non-predecessor-owned-path"
        ]

    _, passing_findings = gate_findings()
    assert passing_findings == []

    violating_gates = (
        "\n\n## Non-predecessor gates\n\n"
        "```bash\n"
        "pytest tests/successor.py::test_owned_node\n"
        "```\n\n"
        "```sh\n"
        "pytest 'tests/quoted.py'\n"
        "```\n\n"
        "```shell\n"
        "pytest \\\n"
        "  tests/continued.py\n"
        "```\n\n"
        "```zsh\n"
        "pytest tests/parallel.py\n"
        "```\n"
    )
    current_path = sections / "section-03-current.md"
    current_path.write_text(section_text("section-03-current", allowed_and_near_miss_gates + violating_gates))

    absent_result, absent_findings = gate_findings()
    expected_paths = {
        "tests/continued.py",
        "tests/parallel.py",
        "tests/quoted.py",
        "tests/successor.py",
    }
    assert absent_result.returncode == 1
    assert len(absent_findings) == len(expected_paths)
    assert {item["severity"] for item in absent_findings} == {"high"}
    for path in expected_paths:
        assert sum(path in item["message"] for item in absent_findings) == 1

    for relative_path in expected_paths:
        target = planning / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# pre-existing target\n")
    existing_result, existing_findings = gate_findings()
    assert existing_result.returncode == 1
    assert existing_findings == absent_findings

    unclosed_gate = (
        "\n\n## Unclosed CommonMark shell fence\n\n"
        "```bash\n"
        "pytest tests/successor.py::test_unclosed_fence\n"
    )
    current_path.write_text(section_text("section-03-current", unclosed_gate))
    unclosed_result, unclosed_findings = lint_findings()
    assert unclosed_result.returncode == 1
    assert [item["code"] for item in unclosed_findings].count("malformed-shell-gate") == 1
    unclosed_gate_findings = [
        item
        for item in unclosed_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]
    assert len(unclosed_gate_findings) == 1
    assert unclosed_gate_findings[0]["severity"] == "high"
    assert "tests/successor.py" in unclosed_gate_findings[0]["message"]

    unmatched_quote_gates = (
        "\n\n## Malformed shell gates\n\n"
        "```bash\n"
        "pytest tests/successor.py \"unterminated\n"
        "```\n\n"
        "```zsh\n"
        "pytest \"tests/quoted.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", unmatched_quote_gates))
    unmatched_result, unmatched_findings = lint_findings()
    assert unmatched_result.returncode == 1
    unmatched_codes = [item["code"] for item in unmatched_findings]
    assert unmatched_codes.count("malformed-shell-gate") == 1
    recovered_gate_findings = [
        item
        for item in unmatched_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]
    assert len(recovered_gate_findings) == 2
    assert {item["severity"] for item in recovered_gate_findings} == {"high"}
    for path in ("tests/quoted.py", "tests/successor.py"):
        assert sum(path in item["message"] for item in recovered_gate_findings) == 1

    valid_heredoc_gates = (
        "\n\n## Literal heredoc bodies\n\n"
        "```bash\n"
        "cat <<'FIRST' <<-\"SECOND\"\n"
        "'\n"
        "tests/successor.py\n"
        "FIRST\n"
        "\t\"\n"
        "\ttests/parallel.py\n"
        "\tSECOND\n"
        "cat <<EOF-DASH\n"
        "'\n"
        "tests/successor.py\n"
        "EOF-DASH\n"
        "cat <<-EOF-DASH\n"
        "\t\"\n"
        "\ttests/parallel.py\n"
        "\tEOF-DASH\n"
        "pytest tests/current.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", valid_heredoc_gates))
    valid_heredoc_result, valid_heredoc_findings = lint_findings()
    assert "malformed-shell-gate" not in {item["code"] for item in valid_heredoc_findings}
    assert not [
        item
        for item in valid_heredoc_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]

    surrounding_gate_paths = (
        "\n\n## Executed paths around a heredoc\n\n"
        "```sh\n"
        "pytest tests/successor.py\n"
        "cat <<BODY\n"
        "tests/unowned-baseline.py\n"
        "BODY\n"
        "pytest tests/parallel.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", surrounding_gate_paths))
    surrounding_result, surrounding_findings = lint_findings()
    assert surrounding_result.returncode == 1
    assert "malformed-shell-gate" not in {item["code"] for item in surrounding_findings}
    surrounding_owned_findings = [
        item
        for item in surrounding_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]
    assert len(surrounding_owned_findings) == 2
    for path in ("tests/parallel.py", "tests/successor.py"):
        assert sum(path in item["message"] for item in surrounding_owned_findings) == 1

    unterminated_heredoc = (
        "\n\n## Unterminated heredoc\n\n"
        "```zsh\n"
        "cat <<'NEVER_CLOSES'\n"
        "tests/successor.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", unterminated_heredoc))
    unterminated_result, unterminated_findings = lint_findings()
    assert unterminated_result.returncode == 1
    assert [item["code"] for item in unterminated_findings].count("malformed-shell-gate") == 1
    assert not [
        item
        for item in unterminated_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]

    dynamic_heredoc = (
        "\n\n## Dynamic heredoc delimiter\n\n"
        "```shell\n"
        "cat <<$(delimiter)\n"
        "tests/successor.py\n"
        "```\n"
    )
    current_path.write_text(section_text("section-03-current", dynamic_heredoc))
    dynamic_result, dynamic_findings = lint_findings()
    assert dynamic_result.returncode == 1
    assert [item["code"] for item in dynamic_findings].count("malformed-shell-gate") == 1
    assert not [
        item
        for item in dynamic_findings
        if item["code"] == "section-gate-non-predecessor-owned-path"
    ]


def test_typescript_fixture_and_invalid_fixture_snapshots() -> None:
    planning = ROOT / "examples" / "typescript-app" / "01-auth"
    assert run_cmd("lint-plan", "--planning-dir", str(planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("lint-sections", "--planning-dir", str(planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("traceability", "--planning-dir", str(planning), "--strict")["score"] == 100
    assert run_cmd("forge-score", "--planning-dir", str(planning), "--depth", "standard", "--strict")["forge_score"] == 100

    saas_planning = ROOT / "examples" / "saas" / "01-authentication"
    assert run_cmd("lint-plan", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("lint-sections", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["score"] == 100
    assert run_cmd("traceability", "--planning-dir", str(saas_planning), "--strict")["score"] == 100
    assert run_cmd("forge-score", "--planning-dir", str(saas_planning), "--depth", "standard", "--strict")["forge_score"] == 100

    missing_index = run_raw("lint-sections", "--planning-dir", str(ROOT / "examples" / "invalid" / "missing-section-index"))
    assert missing_index.returncode != 0
    assert json.loads(missing_index.stdout)["findings"][0]["code"] == "missing-section-index"

    vague = run_raw("lint-sections", "--planning-dir", str(ROOT / "examples" / "invalid" / "vague-section"))
    assert vague.returncode != 0
    codes = {item["code"] for item in json.loads(vague.stdout)["findings"]}
    assert "vague-section-name" in codes
