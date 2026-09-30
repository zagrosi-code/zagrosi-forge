"""Parallel layers respect live file ownership as well as dependency order."""
import json
import os
from pathlib import Path

import pytest

from forge_test_helpers import load_zagrosi_module
from test_resume_guidance import documented_detached_plan


@pytest.fixture
def plan(tmp_path):
    planning = documented_detached_plan(tmp_path / "plan", "lean")
    sections = planning / "sections"
    first = sections / "section-01-normalize.md"
    body = first.read_text()
    first.unlink()
    names = ["section-01-endpoints", "section-02-ui", "section-03-worker"]
    for name in names:
        (sections / f"{name}.md").write_text(body.replace("section-01-normalize", name).replace("labels", name))
    index = sections / "index.md"
    index.write_text(index.read_text().replace("section-01-normalize", "\n".join(names)))
    return planning, names


def schedule(forge, capsys, planning):
    code = forge.entrypoint.main(["parallel-plan", "--planning-dir", str(planning)])
    result = json.loads(capsys.readouterr().out)
    assert code == 0, result
    return result["layers"]


def test_disjoint_sections_stay_parallel_and_expanded_cleanup_ownership_serializes(plan, capsys):
    planning, names = plan
    forge = load_zagrosi_module()
    assert schedule(forge, capsys, planning) == [names]
    second = planning / "sections" / f"{names[1]}.md"
    second.write_text(second.read_text().replace("## Tests first", f"- `src/{names[0]}.py`\n\n## Tests first"))
    assert schedule(forge, capsys, planning) == [[names[0], names[2]], [names[1]]]
    third = planning / "sections" / f"{names[2]}.md"
    third.write_text(third.read_text().replace("## Tests first", f"- `src/{names[0]}.py`\n\n## Tests first"))
    assert schedule(forge, capsys, planning) == [[name] for name in names]


def test_ownership_serialization_does_not_skip_dependency_readiness(plan, capsys):
    planning, names = plan
    index = planning / "sections/index.md"
    index.write_text(index.read_text() + f"\n## Dependency Graph\n\n- {names[0]} depends on {names[2]}.\n")
    assert schedule(load_zagrosi_module(), capsys, planning) == [[names[1], names[2]], [names[0]]]


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_linked_names_of_one_existing_file_never_share_a_layer(plan, capsys, link):
    planning, names = plan
    target = planning.parent / "target"
    (target / "src").mkdir(parents=True)
    source = target / "src" / f"{names[0]}.py"
    source.write_text("existing = True\n")
    alias = source.with_name(f"{names[1]}.py")
    try:
        alias.symlink_to(source) if link == "symbolic" else os.link(source, alias)
    except OSError:
        pytest.skip(f"{link} links are unavailable")
    state = planning / "implementation"
    state.mkdir()
    (state / "zagrosi_implement_config.json").write_text(json.dumps({"target_dir": str(target)}))
    assert schedule(load_zagrosi_module(), capsys, planning) == [[names[0], names[2]], [names[1]]]


@pytest.mark.parametrize("claim", ["case-alias", "parent-directory"])
def test_portable_new_path_and_parent_claims_do_not_overlap(plan, capsys, claim):
    planning, names = plan
    second = planning / "sections" / f"{names[1]}.md"
    shared = f"src/{names[0].upper()}.py" if claim == "case-alias" else "src"
    second.write_text(second.read_text().replace(f"src/{names[1]}.py", shared))
    assert not (planning.parent / shared).exists()
    assert schedule(load_zagrosi_module(), capsys, planning) == [[names[0], names[2]], [names[1]]]


@pytest.mark.parametrize("declaration", ["", "No ownership decided yet.", "```text\nsrc/shared.py\n", "- `../outside.py`\n"])
def test_unknown_or_malformed_ownership_blocks_parallel_plan(plan, capsys, declaration):
    planning, names = plan
    for name in names[:2]:
        path = planning / "sections" / f"{name}.md"
        prefix, rest = path.read_text().split("## Owned files\n", 1)
        _, suffix = rest.split("## Tests first", 1)
        path.write_text(prefix + "## Owned files\n" + declaration + "\n## Tests first" + suffix)
    forge = load_zagrosi_module()
    for name in names[:2]:
        assert forge.ownership.extract_section_owned_paths((planning / "sections" / f"{name}.md").read_text()) == []
    assert not forge.state.mutable_admitted_readiness(planning)["admission"]["success"]
    assert forge.entrypoint.main(["parallel-plan", "--planning-dir", str(planning)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["success"] and result["layers"] == []
    assert "lint-implementation-readiness" in result["admission"]["blocking_gates"]
    readiness = next(gate for gate in result["admission"]["gates"] if gate["name"] == "lint-implementation-readiness")
    assert {Path(item["path"]).stem for item in readiness["payload"]["findings"]
            if item["code"] == "no-file-ownership"} == set(names[:2])
