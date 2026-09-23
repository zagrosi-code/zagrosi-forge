"""Section context keeps complete contracts while excluding unrelated work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def forge():
    path = Path(__file__).resolve().parents[1] / "scripts" / "zagrosi_skills.py"
    from runtime_support import load_runtime
    return load_runtime(path)


SECTION = "section-01-auth"


@pytest.fixture
def plan(tmp_path):
    (tmp_path / "zagrosi_plan_config.json").write_text(json.dumps({"depth_mode": "standard"}))
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / f"{SECTION}.md").write_text(
        "# Authentication\n\nREQ-001: Implement sign-in.\n\n"
        "## Owned files\n\n- `src/auth.py`\n\n"
        "## Tests\n\n`test_local_auth` verifies sign-in.\n"
    )
    (sections / "section-02-billing.md").write_text("# Billing\n\nREQ-002: Bill users.\n")
    (tmp_path / "codex-spec.md").write_text(
        '<!-- FORGE_META\n{"requirement_ids":["REQ-001","REQ-002"]}\nEND_FORGE_META -->\n'
        "# Spec\n\n## REQ-001 Authentication\n\nReject invalid credentials.\n\n"
        "## REQ-002 Billing\n\nCollect card payments.\n"
    )
    (tmp_path / "codex-plan.md").write_text(
        "# Plan\n\n## REQ-001 Authentication\n\nReuse the existing identity provider.\n\n"
        "## REQ-002 Billing\n\nImplement payment settlement.\n"
    )
    (tmp_path / "codex-plan-tdd.md").write_text(
        "# Test matrix\n\n| Requirement | Test |\n| --- | --- |\n"
        "| REQ-001 | `test_selected` |\n| REQ-002 | `test_unrelated` |\n"
    )
    return tmp_path


def test_context_selects_requirement_rows_and_inherited_heading_bodies(forge, plan):
    result = forge.context.build_context(plan, SECTION, 1000)
    assert result["success"]
    content = result["content"]
    assert "Reject invalid credentials." in content
    assert "Reuse the existing identity provider." in content
    assert "| Requirement | Test |\n| --- | --- |" in content
    assert "| REQ-001 | `test_selected` |" in content
    assert "REQ-002" not in content
    assert "test_unrelated" not in content
    assert "FORGE_META" not in content
    assert result["requirements"] == ["REQ-001"]
    assert result["tests"] == ["test_local_auth"]
    assert result["files"] == ["src/auth.py"]


def test_tables_inherit_scope_without_repeating_requirement_ids(forge):
    text = (
        "## REQ-001 Authentication\n\n### Tests\n\n"
        "| Test | Result |\n| --- | --- |\n| `test_inherited` | rejects a bad token |\n\n"
        "## REQ-002 Billing\n\n`test_unrelated` charges a card.\n"
    )
    selected = "\n".join(block for _, block in forge.context.selected_context_blocks(text, {"REQ-001"}))
    assert "test_inherited" in selected
    assert "test_unrelated" not in selected


@pytest.mark.parametrize("first,second", [("-", "-"), ("1.", "2.")])
def test_adjacent_compact_requirement_list_items_are_selected_individually(forge, first, second):
    text = (
        "# Requirements\n\n"
        f"{first} REQ-001: Authenticate users.\n"
        "  - Preserve the nested token validation contract.\n"
        f"{second} REQ-002: Settle payments.\n"
    )
    selected = "\n".join(block for _, block in forge.context.selected_context_blocks(text, {"REQ-001"}))
    assert "Authenticate users." in selected
    assert "Preserve the nested token validation contract." in selected
    assert "REQ-002" not in selected


def test_requirement_matrix_allows_optional_outer_pipes(forge):
    text = (
        "# Tests\n\nRequirement | Test\n--- | ---\n"
        "REQ-001 | `test_selected`\nREQ-002 | `test_unrelated`\n"
    )
    selected = "\n".join(block for _, block in forge.context.selected_context_blocks(text, {"REQ-001"}))
    assert "Requirement | Test\n--- | ---\nREQ-001 | `test_selected`" in selected
    assert "test_unrelated" not in selected


def test_relevant_contract_after_long_unrelated_content_is_selected(forge, plan):
    (plan / "codex-plan.md").write_text(
        "## REQ-002 Billing\n\n" + "Unrelated payment details.\n" * 100
        + "\n## REQ-001 Authentication\n\nLate authentication contract.\n"
    )
    result = forge.context.build_context(plan, SECTION, 1000, line_limit=5)
    assert result["success"]
    assert "Late authentication contract." in result["content"]
    assert "Unrelated payment details." not in result["content"]


def test_complete_section_is_preserved_even_when_artifact_line_limit_is_small(forge, plan):
    section_path = plan / "sections" / f"{SECTION}.md"
    section = section_path.read_text() + "\n## Complete contract\n\n" + "Required behavior.\n" * 100
    section_path.write_text(section)
    result = forge.context.build_context(plan, SECTION, 1000, line_limit=1)
    assert result["success"]
    assert section.rstrip() in result["content"]
    assert result["word_count"] <= 1000


def test_budget_omits_whole_blocks_and_names_their_sources(forge, plan):
    (plan / "codex-plan.md").write_text(
        "## REQ-001 Authentication\n\n" + "important " * 300 + "\n"
    )
    result = forge.context.build_context(plan, SECTION, 140)
    assert result["success"]
    assert result["word_count"] <= 140
    assert "important" not in result["content"]
    assert any(str(plan / "codex-plan.md") in source for source in result["omitted_sources"])
    assert "Omitted plan context:" in result["content"]


def test_insufficient_budget_fails_without_truncating_or_writing(forge, plan, capsys):
    target = plan / "out" / "context.md"
    code = forge.entrypoint.main([
        "context-brief", "--planning-dir", str(plan), "--section", SECTION,
        "--max-words", "1", "--output", str(target),
    ])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert not result["success"]
    assert result["required_words"] > result["max_words"]
    assert "content" not in result
    assert not target.exists()


@pytest.mark.parametrize("section", ["section-99-missing", "../secret", "section-01-auth/extra"])
def test_missing_or_invalid_section_fails_without_packet(forge, plan, capsys, section):
    code = forge.entrypoint.main(["implementation-packet", "--planning-dir", str(plan), "--section", section])
    assert code == 1
    assert not json.loads(capsys.readouterr().out)["success"]
    assert not (plan / ".forge" / "packets").exists()


@pytest.mark.parametrize("max_words,line_limit", [(0, 80), (-1, 80), (1000, 0), (1000, -1)])
def test_nonpositive_context_budgets_fail(forge, plan, max_words, line_limit):
    assert not forge.context.build_context(plan, SECTION, max_words, line_limit)["success"]


@pytest.mark.parametrize("marker", ["FORGE", "DEEP"])
def test_metadata_does_not_become_context_or_requirement_scope(forge, marker):
    text = (
        f'<!-- {marker}_META\n{{"requirement_ids":["REQ-001"]}}\nEND_{marker}_META -->\n'
        "## REQ-002 Billing\n\nUnrelated contract.\n"
    )
    assert forge.context.selected_context_blocks(text, {"REQ-001"}) == []
    all_context = "\n".join(block for _, block in forge.context.selected_context_blocks(text, set()))
    assert "META" not in all_context
    assert "Unrelated contract." in all_context


def test_fences_are_kept_intact_and_info_strings_do_not_close_them(forge):
    code = '```text\nstart\n```not-a-closing-fence\n\n# still code\n\nfinish\n```'
    text = "## REQ-001 Example\n\n" + code + "\n\n## REQ-002 Other\n\nIgnore this.\n"
    selected = forge.context.selected_context_blocks(text, {"REQ-001"})
    assert any(block == code for _, block in selected)
    assert "Ignore this." not in "\n".join(block for _, block in selected)


def test_list_and_table_syntax_inside_fences_remains_literal(forge):
    code = "```markdown\n- first literal item\n- second literal item\n\nColumn | Value\n--- | ---\nfirst | second\n```"
    selected = forge.context.selected_context_blocks("## REQ-001 Example\n\n" + code, {"REQ-001"})
    assert any(block == code for _, block in selected)


def test_oversized_fenced_block_is_omitted_whole(forge, plan):
    (plan / "codex-plan.md").write_text("## REQ-001 Example\n\n```python\n" + "pass\n" * 30 + "```\n")
    result = forge.context.build_context(plan, SECTION, 1000, line_limit=5)
    assert result["success"]
    assert "```" not in result["content"]
    assert any(str(plan / "codex-plan.md") in source for source in result["omitted_sources"])


def test_omitted_test_matrix_does_not_escape_budget_through_metadata(forge, plan):
    (plan / "codex-plan-tdd.md").write_text(
        "# Tests\n\n| Requirement | Test |\n| --- | --- |\n"
        + "".join(f"| REQ-001 | `test_omitted_{number}` |\n" for number in range(50))
    )
    result = forge.context.build_context(plan, SECTION, 1000, line_limit=5)
    assert result["success"]
    assert result["tests"] == ["test_local_auth"]
    assert "`test_omitted_" not in result["content"]
    assert any(str(plan / "codex-plan-tdd.md") in source for source in result["omitted_sources"])


def test_packet_ignores_unrelated_coverage_gaps(forge, plan, capsys):
    (plan / "codex-plan.md").write_text("# Plan\n\nREQ-001: Reuse auth.\n")
    code = forge.entrypoint.main(["implementation-packet", "--planning-dir", str(plan), "--section", SECTION])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["success"]
    packet = plan / ".forge" / "packets" / f"{SECTION}-packet.md"
    assert packet.exists()
    assert "REQ-002" not in packet.read_text()
    assert result["requirements"] == ["REQ-001"]
    assert "test_unrelated" not in result["tests"]
    assert any(item.code == "traceability-gap" for item in forge.traceability.traceability_analysis(plan)[0])


@pytest.mark.parametrize("gap", ["missing-source", "metadata-only", "no-section-requirements", "section-metadata-only"])
def test_selected_coverage_gap_does_not_write_packet(forge, plan, capsys, gap):
    if gap == "missing-source":
        (plan / "codex-plan.md").unlink()
    elif gap == "metadata-only":
        (plan / "codex-plan.md").write_text(
            '<!-- FORGE_META\n{"requirement_ids":["REQ-001"]}\nEND_FORGE_META -->\n'
            "# Plan\n\nREQ-002: Implement billing only.\n"
        )
    elif gap == "section-metadata-only":
        (plan / "sections" / f"{SECTION}.md").write_text(
            '<!-- FORGE_META\n{"requirement_ids":["REQ-001"]}\nEND_FORGE_META -->\n'
            "# Section\n\nNo substantive requirement coverage.\n"
        )
    else:
        (plan / "sections" / f"{SECTION}.md").write_text("# Section\n\nNo identified requirements.\n")
    code = forge.entrypoint.main(["implementation-packet", "--planning-dir", str(plan), "--section", SECTION])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert not result["success"]
    assert not (plan / ".forge" / "packets").exists()


@pytest.mark.parametrize("source", ["spec.md", "requirements.md", "custom-spec.md"])
def test_lean_context_preserves_source_fallback_and_section_tdd(forge, plan, capsys, source):
    (plan / "codex-spec.md").rename(plan / source)
    (plan / "codex-plan-tdd.md").unlink()
    config = {"depth_mode": "lean"}
    if source == "custom-spec.md":
        config["initial_file"] = source
    (plan / "zagrosi_plan_config.json").write_text(json.dumps(config))
    code = forge.entrypoint.main(["implementation-packet", "--planning-dir", str(plan), "--section", SECTION])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["requirements"] == ["REQ-001"]
    assert result["tests"] == ["test_local_auth"]
    content = Path(result["output"]).read_text()
    assert content.startswith((plan / "sections" / f"{SECTION}.md").read_text().strip())
    assert "Reject invalid credentials." in content
    assert source in content
    assert "REQ-002" not in content
    assert "## Sources" not in content
    assert "## Traceability" not in content
    assert "sources" not in result
    assert "omitted_sources" not in result


def test_packet_keeps_test_names_owned_by_the_section(forge, plan, capsys):
    path = plan / "sections" / f"{SECTION}.md"
    path.write_text(path.read_text() + "\nRead `tests/test_module.py`; use `auth_result` as the contract label.\n")
    code = forge.entrypoint.main(["implementation-packet", "--planning-dir", str(plan), "--section", SECTION])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["tests"] == ["test_local_auth"]
    assert result["files"] == ["src/auth.py"]


def test_empty_section_fails_without_creating_output(forge, plan, capsys):
    (plan / "sections" / f"{SECTION}.md").write_text("\n")
    output = plan / "context.md"
    code = forge.entrypoint.main(["context-brief", "--planning-dir", str(plan), "--section", SECTION, "--output", str(output)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert not result["success"]
    assert not output.exists()


def test_written_brief_preserves_null_content_schema_without_duplicate_metadata(forge, plan, capsys):
    output = plan / "context.md"
    code = forge.entrypoint.main(["context-brief", "--planning-dir", str(plan), "--section", SECTION, "--output", str(output)])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["output"] == str(output)
    assert result["content"] is None
    assert "sources" not in result
    assert "omitted_sources" not in result
    assert "tests" not in result
    assert output.read_text().startswith("# Authentication")


def test_shared_requirement_contract_and_heading_cross_reference_are_preserved(forge):
    text = (
        "## REQ-001 Authentication\n\nApply REQ-002 replay protection before creating a session.\n\n"
        "## Shared invariant\n\nREQ-001 and REQ-002 must use the same transaction.\n"
    )
    selected = "\n".join(block for _, block in forge.context.selected_context_blocks(text, {"REQ-001"}))
    assert "Apply REQ-002 replay protection before creating a session." in selected
    assert "REQ-001 and REQ-002 must use the same transaction." in selected


def test_untagged_shared_constraints_have_an_explicit_source_pointer(forge, plan):
    path = plan / "codex-plan.md"
    path.write_text(path.read_text() + "\n## Shared session contract\n\nNever write a session before replay checks.\n")
    result = forge.context.build_context(plan, SECTION, 1000)
    assert result["success"]
    assert f"Omitted plan context: `{path}`." in result["content"]
    assert str(path) in result["omitted_sources"]


def test_missing_planning_directory_does_not_produce_a_blank_success(forge, tmp_path, capsys):
    code = forge.entrypoint.main(["context-brief", "--planning-dir", str(tmp_path / "missing")])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert not result["success"]
    assert "content" not in result


@pytest.mark.parametrize("heading,table", [
    (
        "REQ-001 and REQ-002 shared session contract",
        "| Condition | Result |\n| --- | --- |\n| expired token | reject |",
    ),
    (
        "REQ-001 authentication",
        "| Prerequisite | Rule |\n| --- | --- |\n| REQ-002 replay check | reject failures |",
    ),
])
def test_contract_tables_preserve_inherited_ownership_and_cross_references(forge, heading, table):
    text = f"## {heading}\n\n{table}\n"
    selected = forge.context.selected_context_blocks(text, {"REQ-001"})
    assert any(block == table for _, block in selected)


def test_explicit_decision_and_risk_links_include_complete_cyclic_contracts(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nUse [DEC-004](../codex-plan.md#dec-004-rounding).\n")
    (plan / "codex-plan.md").write_text(
        "# Plan\n\n## DEC-004 Rounding\n\nDiscount aggregate subtotal before tax.\n\n"
        "### Examples\n\n" + "A required example.\n" * 30
        + "\nApply [RISK-002](risks.md#risk-002).\n\n## Other decision\n\nUnrelated billing behavior.\n"
    )
    (plan / "risks.md").write_text(
        "## RISK-002\n\nNever round per item. See [decision](codex-plan.md#dec-004-rounding).\n"
    )
    result = forge.context.build_context(plan, SECTION, 2000, line_limit=1)
    assert result["success"]
    assert result["content"].count("Discount aggregate subtotal before tax.") == 1
    assert result["content"].count("A required example.") == 30
    assert result["content"].count("Never round per item.") == 1
    assert "Unrelated billing behavior." not in result["content"]


@pytest.mark.parametrize("link", ["../missing.md#decision", "../codex-plan.md#missing-anchor"])
def test_broken_explicit_contract_link_fails_without_writing_packet(forge, plan, capsys, link):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + f"\nUse [decision]({link}).\n")
    code = forge.entrypoint.main(["implementation-packet", "--planning-dir", str(plan), "--section", SECTION])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert not result["success"]
    assert "linked" in result["error"]
    assert not (plan / ".forge" / "packets").exists()


def test_linked_contract_over_budget_is_not_omitted(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nUse [decision](../decisions.md#decision).\n")
    (plan / "decisions.md").write_text("## Decision\n\n" + "required " * 500)
    result = forge.context.build_context(plan, SECTION, 200)
    assert not result["success"]
    assert result["required_words"] > result["max_words"]
    assert "content" not in result


def test_self_anchor_keeps_section_once_and_fenced_links_are_literal(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    text = section.read_text() + (
        "\n## Decision\n\nUse [local](#decision).\n"
        "`[inline example](missing.md)`\n\n```markdown\n[example](also-missing.md)\n```\n"
        "External [documentation](https://example.com/docs.md#example).\n"
    )
    section.write_text(text)
    result = forge.context.build_context(plan, SECTION, 2000)
    assert result["success"]
    assert result["content"].count("Use [local](#decision).") == 1
    assert text.rstrip() in result["content"]


@pytest.mark.parametrize("body,expected", [
    ("[before](before.md) <!-- [hidden](missing.md) --> [after](after.md)", ["before.md", "after.md"]),
    ("<!--\n[hidden](missing.md)\n-->\n[`DEC-001`](live.md)", ["live.md"]),
    ("<!-- ` [hidden](missing.md) -->\n[`DEC-001`](live.md)", ["live.md"]),
    ("<!--\n```markdown\n[hidden](missing.md)\n-->\n[live](live.md)", ["live.md"]),
    ("`<!--` [live](live.md)", ["live.md"]),
    ("`literal\n<!-- example\n` [live](\nlive.md\n)", ["live.md"]),
    ("```markdown\n<!-- [example](missing.md)\n```\n[live](live.md)", ["live.md"]),
    ("<!--\n[hidden](missing.md)", []),
])
def test_link_scanning_respects_comment_and_code_boundaries(forge, body, expected):
    assert forge.context_links.local_links(body) == expected


def test_commented_missing_links_do_not_block_context_or_linked_contracts(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() +
                       "\n<!-- [obsolete](../missing.md) -->\n[`DEC-001`](../decisions.md#active)\n")
    (plan / "decisions.md").write_text(
        "## Active\n\nKeep the live contract.\n<!--\n[obsolete](also-missing.md)\n-->\n")
    result = forge.context.build_context(plan, SECTION, 2000)
    assert result["success"], result
    assert "Keep the live contract." in result["content"]


def test_whole_file_link_and_nested_anchor_are_merged(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nRead [all](../decisions.md) and [detail](../decisions.md#detail).\n")
    (plan / "decisions.md").write_text("# Decisions\n\nRoot contract.\n\n## Detail\n\nNested contract.\n")
    result = forge.context.build_context(plan, SECTION, 2000)
    assert result["success"]
    assert result["content"].count("Root contract.") == 1
    assert result["content"].count("Nested contract.") == 1
    assert "omitted_sources" not in result


def test_link_resolution_is_bounded_and_rejects_directory_escape(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\n[escape](../../outside.md#contract)\n")
    assert "leaves the planning directory" in forge.context.build_context(plan, SECTION, 2000)["error"]
    section.write_text("REQ-001: [start](../chain.md#contract-0)\n")
    (plan / "chain.md").write_text("\n".join(
        f"## Contract {number}\n\n[next](#contract-{number + 1})\n" for number in range(66)
    ))
    assert "reference limit" in forge.context.build_context(plan, SECTION, 2000)["error"]


def test_inline_code_link_label_preserves_required_contract(forge, plan):
    section = plan / "sections" / f"{SECTION}.md"
    section.write_text(section.read_text() + "\nUse [`DEC-001`](../rounding.md#decision).\n"
                       "`[literal example](missing.md)`\n")
    (plan / "rounding.md").write_text("## Decision\n\nAlways round aggregate tax down.\n")
    result = forge.context.build_context(plan, SECTION, 2000)
    assert result["success"]
    assert "Always round aggregate tax down." in result["content"]
