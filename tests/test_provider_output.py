"""Packet reviews require finished output and trustworthy optional metadata."""
import argparse
import json

import pytest

from forge_test_helpers import load_zagrosi_module


@pytest.fixture
def review(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    packet, output = tmp_path / "packet.md", tmp_path / "review.json"
    packet.write_text("Review src/amount.py: preserve error ordering.")
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)

    def run(provider, payload):
        calls = []
        raw = "\n".join(json.dumps(event) for event in payload) if provider == "codex" else json.dumps(payload)

        def execute(command, workspace, **kwargs):
            calls.append(workspace)
            return {"returncode": 0, "timed_out": False, "seconds": .1, "stdout": raw}

        monkeypatch.setattr(forge.providers, "execute", execute)
        options = argparse.Namespace(provider=provider, model="selected", input=str(packet), output=str(output),
                                     timeout=5, adapter=None)
        code = forge.providers.provider_review(options)
        report = json.loads(output.read_text())
        summary = json.loads(capsys.readouterr().out)
        assert code == (0 if report["success"] else 1)
        assert summary["success"] == report["success"] and "review" not in summary
        assert len(calls) == 1 and not calls[0].exists()
        assert packet.read_text() == "Review src/amount.py: preserve error ordering."
        assert report["requested_model"] == "selected"
        return report

    return run


def item(kind, text="Completed review.", identity="message-1", item_type="agent_message"):
    return {"type": "item." + kind, "item": {"id": identity, "type": item_type, "text": text}}


DONE = {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 4}}


@pytest.mark.parametrize("events", [
    [item("started"), DONE],
    [item("updated"), DONE],
    [item("completed"), DONE, {"type": "turn.started"}],
    [item("completed"), DONE, item("started", identity="message-2")],
    [item("completed"), DONE, item("completed", identity="message-2")],
    [{"type": "turn.started"}, {"type": "turn.started"}, item("completed"), DONE],
    [item("started", identity="unfinished"), item("completed"), DONE],
    [item("completed"), {"type": "turn.started"}, DONE],
    [item("completed"), item("completed"), DONE],
    [item("completed"), item("started"), DONE],
    [item("started", identity=None), item("completed"), DONE],
], ids=["started-only", "updated-only", "trailing-turn", "trailing-item-start", "trailing-item-end",
        "overlapping-turns", "unfinished-item", "late-turn-start", "duplicate-completion", "restarted-item",
        "unidentified-start"])
def test_unfinished_or_ambiguous_codex_reviews_fail(review, events):
    report = review("codex", events)
    assert not report["success"] and "Codex" in report["error"]
    assert "review" not in report


@pytest.mark.parametrize("kind", ["started", "updated", "completed"])
@pytest.mark.parametrize("payload", [None, [], False, "partial"])
def test_malformed_codex_items_cannot_hide_activity(review, kind, payload):
    report = review("codex", [{"type": "item." + kind, "item": payload}, item("completed"), DONE])
    assert not report["success"] and "item lifecycle" in report["error"]


@pytest.mark.parametrize("kind", ["started", "updated", "completed"])
def test_ignored_codex_item_text_does_not_hide_tool_attempts(review, kind):
    report = review("codex", [item(kind, item_type="command_execution"), item("completed"), DONE])
    assert not report["success"] and "tool activity" in report["error"]


@pytest.mark.parametrize("text", [None, [], False, 123])
def test_malformed_final_codex_text_cannot_leave_only_an_earlier_review(review, text):
    report = review("codex", [item("completed"), item("completed", text, "message-2"), DONE])
    assert not report["success"] and "review text" in report["error"]


@pytest.mark.parametrize("initial,changed", [("agent_message", "reasoning"), ("reasoning", "agent_message")])
@pytest.mark.parametrize("kind", ["updated", "completed"])
def test_codex_pending_items_cannot_change_type(review, initial, changed, kind):
    events = [item("completed", "Earlier review.", "prior"),
              item("started", item_type=initial), item(kind, item_type=changed)]
    if kind == "updated":
        events.append(item("completed", item_type=initial))
    report = review("codex", [*events, DONE])
    assert not report["success"] and "item type changed" in report["error"]
    assert "review" not in report


def test_codex_review_uses_only_completed_messages_once(review):
    events = [
        {"type": "thread.started", "thread_id": "thread-1", "model": "selected"},
        {"type": "turn.started"},
        item("started", "Thinking.", "reasoning-1", "reasoning"),
        item("started", "Partial draft."),
        item("updated", "Still thinking.", "reasoning-1", "reasoning"),
        item("updated", "Updated draft."),
        item("completed", "Finished reasoning.", "reasoning-1", "reasoning"),
        item("completed", "Preserve error ordering."),
        item("completed", "Cover empty input.", "message-2"),
        DONE,
    ]
    report = review("codex", events)
    assert report["success"] and report["review"] == "Preserve error ordering.\n\nCover empty input."
    assert report["model_identity"] == "reported" and report["observed_models"] == ["selected"]
    assert report["usage"] == DONE["usage"]


@pytest.mark.parametrize("metadata", ["claude-models", "gemini-stats", "gemini-models", "gemini-tools"])
@pytest.mark.parametrize("value", [[], ["other-model"], False, 0, ""])
def test_malformed_optional_metadata_is_not_treated_as_unreported(review, metadata, value):
    if metadata == "claude-models":
        provider, payload = "claude", {"type": "result", "subtype": "success", "result": "Review.",
                                       "modelUsage": value}
    else:
        provider = "gemini"
        stats = value if metadata == "gemini-stats" else {metadata.removeprefix("gemini-"): value}
        payload = {"response": "Review.", "stats": stats}
    report = review(provider, payload)
    assert not report["success"] and "Invalid" in report["error"]
    assert "review" not in report


@pytest.mark.parametrize("provider,payload", [
    ("claude", {"type": "result", "subtype": "success", "result": "Review."}),
    ("claude", {"type": "result", "subtype": "success", "result": "Review.", "modelUsage": None}),
    ("gemini", {"response": "Review."}),
    ("gemini", {"response": "Review.", "stats": None}),
    ("gemini", {"response": "Review.", "stats": {"models": None, "tools": None}}),
])
def test_missing_or_null_metadata_keeps_supported_unreported_identity(review, provider, payload):
    report = review(provider, payload)
    assert report["success"] and report["review"] == "Review."
    assert report["observed_models"] == [] and report["model_identity"] == "unreported"


@pytest.mark.parametrize("count", [[], False, "", -1, float("nan"), float("inf")])
def test_malformed_gemini_tool_counts_cannot_hide_activity(review, count):
    report = review("gemini", {"response": "Review.", "stats": {"tools": {"totalCalls": count}}})
    assert not report["success"] and "tool" in report["error"]


@pytest.mark.parametrize("count", [None, 0, 0.0, 1, 1.0])
def test_supported_gemini_tool_counts_keep_tool_activity_check(review, count):
    report = review("gemini", {"response": "Review.", "stats": {"tools": {"totalCalls": count}}})
    assert report["success"] is (not bool(count))
    if count:
        assert "tool activity" in report["error"]
