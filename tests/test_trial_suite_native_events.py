"""Independent pure native-event controls; synthetic JSONL is never qualification."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from coding_trial_native import check_native_events, entry_read_command


ENTRY_BYTES = "Use the selected entry.\nUnicode: café.\n".encode()
INSTALLED_PATH = "/codex/plugins/cache/forge-evaluator/neutral-product/2.7.1"
SELECTED_ENTRY = "skills/review's work/SKILL.md"
READER = "from pathlib import Path; import sys; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())"
ENTRY_COMMAND = shlex.join(["/usr/local/bin/python3", "-I", "-B", "-c", READER,
                            INSTALLED_PATH + "/" + SELECTED_ENTRY])
ACTION_COMMAND = shlex.join(["/usr/local/bin/python3", "-I", "-B", "-c",
                             "from pathlib import Path; Path('/workspace/control.txt').write_text('fresh challenge')"])


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Native pure helpers must not launch processes")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def command_event(identifier, script, output=""):
    return {"type": "item.completed", "item": {
        "id": identifier, "type": "command_execution", "command": shlex.join(["/bin/sh", "-c", script]),
        "aggregated_output": output, "exit_code": 0, "status": "completed"}}


def collab_event(identifier, tool, child="child-1", child_status="running"):
    return {"type": "item.completed", "item": {
        "id": identifier, "type": "collab_tool_call", "tool": tool, "sender_thread_id": "parent-1",
        "receiver_thread_ids": [child], "prompt": "Complete the bounded synthetic action.",
        "agents_states": {child: {"status": child_status, "message": None}}, "status": "completed"}}


def valid_events():
    return [command_event("entry-1", ENTRY_COMMAND, ENTRY_BYTES.decode()),
            command_event("action-1", ACTION_COMMAND),
            collab_event("spawn-1", "spawn_agent"),
            collab_event("wait-1", "wait", child_status="completed")]


def process_record(events):
    return {"returncode": 0, "stdout": "\n".join(json.dumps(event) for event in events) + "\n", "stderr": "",
            "timed_out": False, "termination_error": None, "stdout_truncated": False, "stderr_truncated": False}


def check(process, *, entry_command=ENTRY_COMMAND, entry_bytes=ENTRY_BYTES, subagents=True):
    before = deepcopy(process)
    result = check_native_events(process, entry_command=entry_command, entry_bytes=entry_bytes,
                                 action_command=ACTION_COMMAND, subagents=subagents)
    assert process == before
    assert set(result) == {"entry-routing", "required-tools", "subagent-execution"}
    for observation in result.values():
        assert set(observation) == {"status", "detail", "event_ids"}
        assert observation["status"] in {"passed", "failed", "not_applicable"}
        assert isinstance(observation["detail"], str)
        assert isinstance(observation["event_ids"], list)
        assert all(isinstance(value, str) and value for value in observation["event_ids"])
        assert len(observation["event_ids"]) == len(set(observation["event_ids"]))
    return result


def test_complete_exact_entry_action_and_same_child_observations_pass():
    events = [{"type": "thread.started", "thread_id": "parent-1"}, *valid_events(),
              {"type": "item.completed", "item": {"id": "message-1", "type": "agent_message", "text": "Done."}}]
    process = process_record(events)
    process["stdout"] = "\n" + process["stdout"] + "\n"
    result = check(process)
    assert {name: row["status"] for name, row in result.items()} == {
        "entry-routing": "passed", "required-tools": "passed", "subagent-execution": "passed"}
    assert result["entry-routing"]["event_ids"] == ["entry-1"]
    assert result["required-tools"]["event_ids"] == ["action-1"]
    assert set(result["subagent-execution"]["event_ids"]) == {"spawn-1", "wait-1"}


def test_valid_item_lifecycle_may_reuse_its_id_until_terminal_completion():
    events = valid_events()
    started, updated = deepcopy(events[0]), deepcopy(events[0])
    started["type"], updated["type"] = "item.started", "item.updated"
    for event in (started, updated):
        event["item"].update(status="in_progress", exit_code=None, aggregated_output="")
    result = check(process_record([started, updated, *events]))
    assert all(row["status"] == "passed" for row in result.values())
    assert result["entry-routing"]["event_ids"] == ["entry-1"]


@pytest.mark.parametrize("index,observation", [(0, "entry-routing"), (1, "required-tools"),
                                               (2, "subagent-execution"), (3, "subagent-execution")])
@pytest.mark.parametrize("identifier", [None, "", 17])
def test_matched_events_need_real_nonempty_string_ids(index, observation, identifier):
    events = valid_events()
    if identifier is None:
        del events[index]["item"]["id"]
    else:
        events[index]["item"]["id"] = identifier
    assert check(process_record(events))[observation]["status"] == "failed"


def test_empty_product_entry_is_distinct_from_plain_absence():
    events = valid_events()
    events[0]["item"]["aggregated_output"] = ""
    assert check(process_record(events), entry_bytes=b"")["entry-routing"]["status"] == "passed"


@pytest.mark.parametrize("change", ["printf", "different-file", "appended-command", "login-shell", "different-shell",
                                    "started-only", "nonterminal-item", "nonzero", "boolean-exit", "wrong-output"])
def test_same_output_or_similar_command_is_not_the_frozen_entry_read(change):
    events = valid_events()
    event, item = events[0], events[0]["item"]
    if change == "printf":
        item["command"] = shlex.join(["/bin/sh", "-c", shlex.join(["printf", "%s", ENTRY_BYTES.decode()])])
    elif change == "different-file":
        other = ENTRY_COMMAND.replace("/neutral-product/", "/other-product/")
        item["command"] = shlex.join(["/bin/sh", "-c", other])
    elif change == "appended-command":
        item["command"] = shlex.join(["/bin/sh", "-c", ENTRY_COMMAND + "; true"])
    elif change == "login-shell":
        item["command"] = shlex.join(["/bin/sh", "-lc", ENTRY_COMMAND])
    elif change == "different-shell":
        item["command"] = shlex.join(["/bin/bash", "-c", ENTRY_COMMAND])
    elif change == "started-only":
        event["type"] = "item.started"
    elif change == "nonterminal-item":
        item["status"] = "in_progress"
    elif change == "nonzero":
        item["exit_code"] = 1
    elif change == "boolean-exit":
        item["exit_code"] = False
    else:
        item["aggregated_output"] += "extra output\n"
    assert check(process_record(events))["entry-routing"]["status"] == "failed"


def test_undecodable_entry_bytes_fail_entry_without_claiming_automatic_dispatch():
    result = check(process_record(valid_events()), entry_bytes=b"\xff")
    assert result["entry-routing"]["status"] == "failed"


@pytest.mark.parametrize("kind", ["message", "quoted-action", "other-command"])
def test_required_action_needs_its_exact_terminal_command(kind):
    events = valid_events()
    if kind == "message":
        events[1] = {"type": "item.completed", "item": {"id": "action-1", "type": "agent_message", "text": ACTION_COMMAND}}
    else:
        script = shlex.join(["printf", "%s", ACTION_COMMAND]) if kind == "quoted-action" else "true"
        events[1] = command_event("action-1", script)
    assert check(process_record(events))["required-tools"]["status"] == "failed"


@pytest.mark.parametrize("field,value", [("returncode", 1), ("returncode", False), ("timed_out", True),
                                        ("termination_error", "Child termination unconfirmed"),
                                        ("stdout_truncated", True), ("stderr_truncated", True)])
def test_incomplete_or_failed_process_never_yields_positive_action_evidence(field, value):
    process = process_record(valid_events())
    process[field] = value
    assert all(row["status"] == "failed" for row in check(process).values())


@pytest.mark.parametrize("line", ["not JSON", "[]", "null", "true", "{}", '{"type":17}',
                                  '{"type":"thread.started","type":"thread.started","thread_id":"p"}',
                                  '{"type":"item.completed","item":[]}',
                                  '{"type":"thread.started","thread_id":NaN}'])
def test_malformed_or_duplicate_jsonl_anywhere_invalidates_positive_observations(line):
    process = process_record(valid_events())
    process["stdout"] += line + "\n"
    assert all(row["status"] == "failed" for row in check(process).values())


@pytest.mark.parametrize("change", ["different-child", "completed-before-spawn", "still-running", "spawn-failed",
                                    "started-spawn", "no-spawn", "sender-as-child", "v2-empty-completion"])
def test_subagents_require_later_completion_of_the_actual_new_spawned_receiver(change):
    events = valid_events()
    if change == "different-child":
        events[-1] = collab_event("wait-1", "wait", child="child-2", child_status="completed")
    elif change == "completed-before-spawn":
        events[-2:] = list(reversed(events[-2:]))
    elif change == "still-running":
        events[-1]["item"]["agents_states"]["child-1"]["status"] = "running"
    elif change == "spawn-failed":
        events[-2]["item"]["status"] = "failed"
    elif change == "started-spawn":
        events[-2]["type"] = "item.started"
    elif change == "no-spawn":
        del events[-2]
    elif change == "sender-as-child":
        events[-2:] = [collab_event("spawn-1", "spawn_agent", "parent-1"),
                       collab_event("wait-1", "wait", "parent-1", "completed")]
    else:
        events[-1]["item"].update(receiver_thread_ids=[], agents_states={})
    assert check(process_record(events))["subagent-execution"]["status"] == "failed"


def test_plain_entry_and_undeclared_subagents_are_not_applicable():
    result = check(process_record([command_event("action-1", ACTION_COMMAND)]), entry_command=None,
                   entry_bytes=None, subagents=False)
    assert result["entry-routing"]["status"] == "not_applicable"
    assert result["required-tools"]["status"] == "passed"
    assert result["subagent-execution"]["status"] == "not_applicable"


@pytest.mark.parametrize("known_receiver", ["root-thread", "earlier-receiver"])
def test_spawn_cannot_relabel_an_already_observed_thread_as_new(known_receiver):
    events = valid_events()
    if known_receiver == "root-thread":
        events.insert(0, {"type": "thread.started", "thread_id": "child-1"})
    else:
        events.insert(0, collab_event("seen-1", "send_input"))
    assert check(process_record(events))["subagent-execution"]["status"] == "failed"


def test_unrelated_envelope_payload_is_not_a_collaboration_item():
    events = [{"type": "turn.started", "item": True}, *valid_events()]
    assert all(row["status"] == "passed" for row in check(process_record(events)).values())


def test_entry_read_command_uses_explicit_installed_path_and_posix_quotes():
    # The full runtime reader owns validation; this helper receives its selected
    # installation identity and must not guess another product or first skill.
    runtime = {"installed": {"path": INSTALLED_PATH}, "selected_entry": SELECTED_ENTRY}
    assert entry_read_command(runtime) == ENTRY_COMMAND
    assert entry_read_command({"installed": None, "selected_entry": None}) is None
