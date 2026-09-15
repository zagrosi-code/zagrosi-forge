"""Completed pinners are reopened once per pass, with exact dependency closure."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from detached_test_support import detached_record_arguments, make_detached_record_fixture
from forge_test_helpers import SCRIPT, run_cmd
from runtime_support import load_runtime


@pytest.fixture
def graph(tmp_path, monkeypatch):
    forge = load_runtime(SCRIPT)
    owner = forge.pinners
    config = {field: "sha256:" + "a" * 64 for field in (
        "planning_tree_sha256", "admission_pinner_sha256", "admission_state_sha256",
        "detached_implementation_root_identity_digest", "target_root_identity_digest",
        "implement_tool_sha256", "implement_skill_sha256", "implement_test_sha256",
    )}
    config["planning_dir"] = str(tmp_path)
    names = [f"section-{number:02d}-capability" for number in range(1, 41)]
    dependencies, completed, pinners = {}, {}, {}
    (tmp_path / "pinners").mkdir()

    def store(section, pinner):
        raw = forge.handoff_wire.canonical_json_bytes(pinner)
        record = forge.transaction_state.pinner_state_record(pinner, raw)
        path = tmp_path / record["pinner_path"]
        path.write_bytes(raw)
        path.chmod(0o600)
        completed[section] = record
        pinners[section] = pinner
        return raw

    for index, section in enumerate(names):
        dependencies[section] = names[max(0, index - 3):index]
        pinner = dict.fromkeys(forge.detached_contract.SECTION_PINNER_FIELDS)
        pinner.update({key: value for key, value in config.items() if key != "planning_dir"})
        pinner.update(
            schema=forge.detached_contract.SECTION_PINNER_SCHEMA,
            section=section,
            completed_at="2026-09-15T12:00:00Z",
            commit="abc123", commit_status="recorded", notes="Verified",
            files_changed=["src/feature.py"], test_files=["tests/test_feature.py"],
            review_artifacts=[{"path": "code_review/review.md", "sha256": "sha256:" + "b" * 64, "size": 10}],
            evidence_rows=[{"name": "test", "path": "evidence/gate.json", "sha256": "sha256:" + "c" * 64, "size": 20}],
            verification=["pytest tests/test_feature.py"],
            predecessor_pinners=[{
                "section": previous,
                "pinner_path": completed[previous]["pinner_path"],
                "pinner_file_sha256": completed[previous]["pinner_file_sha256"],
            } for previous in dependencies[section]],
        )
        store(section, pinner)

    monkeypatch.setattr(owner._detached_state, "load_detached_state", lambda *_: {"completed_sections": completed})
    monkeypatch.setattr(owner._sections, "dependency_graph", lambda *_: dependencies)
    reads = Counter()
    load = owner._secure_io.load_canonical_json_at

    def counted(fd, path):
        reads[path] += 1
        return load(fd, path)

    monkeypatch.setattr(owner._secure_io, "load_canonical_json_at", counted)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield SimpleNamespace(
            forge=forge, root=tmp_path, fd=fd, config=config, names=names,
            dependencies=dependencies, completed=completed, pinners=pinners,
            reads=reads, store=store, progress={"sections": names},
        )
    finally:
        os.close(fd)


def test_completed_pinners_are_loaded_once_in_each_fresh_pass(graph):
    for count in (1, 2):
        assert graph.forge.pinners.detached_completed_records(graph.fd, graph.config, graph.progress) == graph.completed
        assert graph.reads == Counter({record["pinner_path"]: count for record in graph.completed.values()})


def test_record_keeps_completion_evidence_identical_in_state_and_canonical_pinner(tmp_path):
    fixture = make_detached_record_fixture(tmp_path)
    result = run_cmd(*detached_record_arguments(
        fixture,
        "--notes", "Verified completion",
        "--file", "src/feature.py",
        "--test-file", "tests/test_feature.py",
        "--evidence-row", "test=evidence/record-gate.json",
    ))
    record = result["record"]
    raw = (fixture.implementation_root / record["pinner_path"]).read_bytes()
    pinner = json.loads(raw)
    assert record == {
        **{field: pinner[field] for field in (
            "completed_at", "commit", "commit_status", "notes", "files_changed", "test_files",
            "review_artifacts", "evidence_rows", "verification",
        )},
        "pinner_file_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "pinner_path": f"pinners/{fixture.section}-{hashlib.sha256(raw).hexdigest()}.json",
    }
    assert record["commit"] == "abc123"
    assert record["notes"] == "Verified completion"
    assert record["files_changed"] == ["src/feature.py"]
    assert record["test_files"] == ["tests/test_feature.py"]
    assert len(record["review_artifacts"]) == 2
    assert record["evidence_rows"][0]["name"] == "test"
    assert record["verification"] == ["uv run pytest tests/test_section.py"]
    state = json.loads((fixture.implementation_root / "zagrosi_implement_state.json").read_bytes())
    assert state["completed_sections"][fixture.section] == record


@pytest.mark.parametrize("change, code", [
    ("missing", "predecessor-pinner-current-state-mismatch"),
    ("stale", "predecessor-pinner-current-state-mismatch"),
    ("duplicate", "predecessor-pinner-current-state-mismatch"),
    ("reordered", "predecessor-pinner-current-state-mismatch"),
    ("extra-field", "invalid-section-pinner"),
    ("invalid-type", "invalid-section-pinner"),
    ("absent-state", "predecessor-pinner-current-state-mismatch"),
])
def test_completed_pinners_require_exact_current_predecessor_rows(graph, change, code):
    section = graph.names[-1]
    pinner = graph.pinners[section]
    rows = pinner["predecessor_pinners"]
    if change == "missing":
        rows.pop()
    elif change == "stale":
        rows[0]["pinner_file_sha256"] = "sha256:" + "0" * 64
    elif change == "duplicate":
        rows.append(dict(rows[0]))
    elif change == "reordered":
        rows.reverse()
    elif change == "extra-field":
        rows[0]["extra"] = True
    elif change == "invalid-type":
        rows[0]["section"] = 123
    else:
        del graph.completed[graph.names[0]]
    graph.store(section, pinner)
    with pytest.raises(graph.forge.models.DetachedImplementationError) as caught:
        graph.forge.pinners.detached_completed_records(graph.fd, graph.config, graph.progress)
    assert caught.value.code == code


def test_pending_pinner_uses_staged_bytes_and_validates_dependencies(graph):
    section = graph.names[-1]
    path = graph.root / graph.completed[section]["pinner_path"]
    raw = path.read_bytes()
    path.unlink()
    pending = (section, graph.pinners[section], raw)
    assert graph.forge.pinners.detached_completed_records(graph.fd, graph.config, graph.progress, pending_pinner=pending) == graph.completed
    assert sum(graph.reads.values()) == len(graph.names) - 1
    bad_raw = raw.replace(b"Verified", b"Tampered")
    with pytest.raises(graph.forge.models.DetachedImplementationError) as caught:
        graph.forge.pinners.detached_completed_records(graph.fd, graph.config, graph.progress, pending_pinner=(section, graph.pinners[section], bad_raw))
    assert caught.value.code == "pinner-drift"


@pytest.mark.parametrize("missing", [False, True])
def test_fresh_pass_and_standalone_check_detect_predecessor_file_changes(graph, missing):
    graph.forge.pinners.detached_completed_records(graph.fd, graph.config, graph.progress)
    predecessor, successor = graph.names[:2]
    path = graph.root / graph.completed[predecessor]["pinner_path"]
    if missing:
        path.unlink()
    else:
        changed = {**graph.pinners[predecessor], "notes": "Changed after first pass"}
        path.write_bytes(graph.forge.handoff_wire.canonical_json_bytes(changed))
    with pytest.raises(graph.forge.models.DetachedImplementationError) as standalone:
        graph.forge.pinners.verify_section_pinner(graph.fd, graph.config, successor, graph.completed[successor])
    assert standalone.value.code == ("unsafe-detached-file" if missing else "predecessor-pinner-drift")
    with pytest.raises(graph.forge.models.DetachedImplementationError) as fresh:
        graph.forge.pinners.detached_completed_records(graph.fd, graph.config, graph.progress)
    assert fresh.value.code == ("unsafe-detached-file" if missing else "pinner-drift")
