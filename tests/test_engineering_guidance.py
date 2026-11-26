"""Check the engineering guidance reached by local and tool-free reviewers."""
import doctest
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from forge_test_helpers import ROOT, run_cmd


def test_worked_example_is_reachable_by_writer_and_reviewer(tmp_path):
    guide = ROOT / "skills/zagrosi-implement/references/engineering.md"
    instructions = (ROOT / "skills/zagrosi-plan/SKILL.md", ROOT / "skills/zagrosi-implement/SKILL.md",
                    ROOT / "agents/forge-reviewer.md")
    for path in instructions:
        links = re.findall(r"\]\(([^)]+engineering\.md)\)", path.read_text(encoding="utf-8"))
        assert any((path.parent / link).resolve() == guide for link in links), path
    generated = run_cmd("agent-prompts", "--planning-dir", str(tmp_path), "--type", "implementation-reviewer")
    assert str(guide) in Path(generated["prompt_files"][0]).read_text(encoding="utf-8")

    example = guide.with_name("lookup-example.md")
    assert f"]({example.name})" in guide.read_text(encoding="utf-8")
    result = doctest.testfile(str(example), module_relative=False, encoding="utf-8")
    assert result.attempted > 0 and result.failed == 0


def test_tool_free_reviewer_receives_engineering_guidance_in_its_packet(tmp_path):
    packet = tmp_path / "change.md"
    packet.write_text("Review duplicate lookups. The manifest permits 2.3.0 so it must be current.", encoding="utf-8")
    adapter = tmp_path / "adapter.json"
    echo = "import json, sys; print(json.dumps({'success': True, 'review': sys.stdin.read()}))"
    adapter.write_text(json.dumps({"argv": [sys.executable, "-c", echo]}), encoding="utf-8")
    output = tmp_path / "review.json"

    run_cmd("provider-review", "--provider", "echo", "--adapter", str(adapter),
            "--input", str(packet), "--output", str(output))
    prompt = json.loads(output.read_text(encoding="utf-8"))["review"]
    instructions, supplied = prompt.rsplit("\n\n", 1)
    assert supplied == packet.read_text(encoding="utf-8")
    assert "workload" in instructions and "operation count" in instructions
    assert "latest stable" in instructions and "resolved version" in instructions
    assert "registry" in instructions and "constraint" in instructions


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the npm example")
def test_dependency_example_distinguishes_declared_locked_and_installed(tmp_path):
    """Each version differs so substituting a range or the wrong tree fails."""
    package = "example-linter"
    installed = tmp_path / "node_modules" / package
    installed.mkdir(parents=True)
    (installed / "package.json").write_text(json.dumps({"name": package, "version": "2.5.0"}))
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {package: "^2.3.0"}}))
    lock = {"lockfileVersion": 3, "packages": {"node_modules/" + package: {"version": "2.9.1"}}}
    (tmp_path / "package-lock.json").write_text(json.dumps(lock))

    guide = ROOT / "skills/zagrosi-implement/references/engineering.md"
    section = guide.read_text(encoding="utf-8").split("## Dependency versions\n", 1)[1].split("\n## ", 1)[0]
    probe, = re.findall(r"^```javascript\n(.*?)\n```$", section, re.M | re.S)
    output = subprocess.run(["node", "-e", probe], cwd=tmp_path, capture_output=True,
                            text=True, check=True, timeout=15)
    assert json.loads(output.stdout) == {"declared": "^2.3.0", "locked": "2.9.1", "installed": "2.5.0"}
