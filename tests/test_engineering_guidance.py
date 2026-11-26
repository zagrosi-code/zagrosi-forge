"""Check the engineering guidance reached by local and tool-free reviewers."""
import doctest
import json
from pathlib import Path
import re
import sys

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


def test_tool_free_reviewer_receives_performance_guidance_in_its_packet(tmp_path):
    packet = tmp_path / "change.md"
    packet.write_text("Review duplicate lookups while streaming order lines.", encoding="utf-8")
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
