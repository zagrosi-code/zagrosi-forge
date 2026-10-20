"""Failed provider requests keep the provider's own report; stderr stays private."""
import argparse
import json

import pytest

from forge_test_helpers import load_zagrosi_module

# Captured from `claude -p --output-format json` 2.1.304 with a logged-out CLI (ids zeroed, usage trimmed).
CLAUDE_AUTH_FAILURE = json.dumps({
    "duration_api_ms": 0, "stop_reason": "stop_sequence", "session_id": "00000000-0000-4000-8000-000000000000",
    "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {}, "permission_denials": [],
    "terminal_reason": "api_error", "is_error": True, "num_turns": 1, "subtype": "success", "api_error_status": None,
    "result": "Failed to authenticate: OAuth session expired and could not be refreshed", "type": "result",
    "duration_ms": 28, "uuid": "00000000-0000-4000-8000-000000000001",
})
# Captured from `codex exec --json` (codex-cli 0.154.0) with an unsupported model (thread id zeroed).
CODEX_API_ERROR = json.dumps({"type": "error", "status": 400, "error": {
    "type": "invalid_request_error",
    "message": "The 'forge-probe-nonexistent-model' model is not supported when using Codex with a ChatGPT account."}})
CODEX_MODEL_FAILURE = "\n".join(json.dumps(event) for event in [
    {"type": "thread.started", "thread_id": "00000000-0000-4000-8000-000000000002"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": (
        "Model metadata for `forge-probe-nonexistent-model` not found. "
        "Defaulting to fallback metadata; this can degrade performance and cause issues.")}},
    {"type": "turn.started"},
    {"type": "error", "message": CODEX_API_ERROR},
    {"type": "turn.failed", "error": {"message": CODEX_API_ERROR}},
])


@pytest.fixture
def failed_review(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    source, output = tmp_path / "packet.md", tmp_path / "review.json"
    source.write_text("Review the public API without changing its behavior.")
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)

    def run(provider, stdout, *, pretty=False):
        monkeypatch.setattr(forge.providers, "execute", lambda *args, **kwargs: {
            "returncode": 1, "timed_out": False, "seconds": .1, "stdout": stdout, "stderr": "private@example.com secret"})
        if pretty:
            assert forge.entrypoint.main(["provider-review", "--provider", provider, "--input", str(source),
                                          "--output", str(output), "--pretty"]) == 1
            return capsys.readouterr().out
        options = argparse.Namespace(provider=provider, model=None, input=str(source), output=str(output),
                                     timeout=5, adapter=None)
        assert forge.providers.provider_review(options) == 1
        summary = json.loads(capsys.readouterr().out)
        saved = output.read_text()
        assert "secret" not in saved and "private@" not in saved
        report = json.loads(saved)
        assert "exit 1" in report["error"] and not report["success"]
        assert {key: summary[key] for key in ("failure_kind", "provider_error")} == {
            key: report[key] for key in ("failure_kind", "provider_error")}
        return report

    return run


def test_claude_authentication_failure_keeps_reported_error(failed_review):
    report = failed_review("claude", CLAUDE_AUTH_FAILURE)
    assert report["failure_kind"] == "authentication"
    assert report["provider_error"] == "Failed to authenticate: OAuth session expired and could not be refreshed"
    assert report["login_argv"] == ["claude", "auth", "login"]
    assert "sign in" in report["recovery"].lower()


def test_codex_request_failure_unwraps_api_error(failed_review):
    report = failed_review("codex", CODEX_MODEL_FAILURE)
    assert report["failure_kind"] == "request_failed"
    assert report["provider_error"] == (
        "The 'forge-probe-nonexistent-model' model is not supported when using Codex with a ChatGPT account.")
    assert "login" not in report["recovery"].lower() and "login_argv" not in report


def test_reported_error_is_bounded_and_printable(failed_review):
    envelope = json.loads(CLAUDE_AUTH_FAILURE)
    envelope["result"] = "\x1b[31m" + "x" * 2000 + "\r\n"
    report = failed_review("claude", json.dumps(envelope))
    assert report["failure_kind"] == "request_failed"
    assert 0 < len(report["provider_error"]) <= 300
    assert all(character.isprintable() for character in report["provider_error"])


def test_readable_report_shows_provider_error_and_login(failed_review):
    text = failed_review("claude", CLAUDE_AUTH_FAILURE, pretty=True)
    assert "Failure: authentication" in text
    assert "Provider error: Failed to authenticate: OAuth session expired" in text
    assert "Login: claude auth login" in text


def test_codex_retry_notices_do_not_hide_the_final_error(failed_review):
    retry = json.dumps({"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion)"})
    report = failed_review("codex", retry + "\n" + CODEX_MODEL_FAILURE)
    assert report["provider_error"].startswith("The 'forge-probe-nonexistent-model' model is not supported")
