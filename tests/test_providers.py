"""Native envelopes, explicit adapters and bounded review failure semantics."""
import argparse
import json
from pathlib import Path
import sys

import pytest

from forge_test_helpers import load_zagrosi_module, run_raw


@pytest.fixture
def forge():
    return load_zagrosi_module()


def args(tmp_path, **changes):
    source = tmp_path / "packet.md"
    source.write_text("Review src/amount.py: public errors must be preserved.")
    return argparse.Namespace(provider="claude", model=None, input=str(source),
                              output=str(tmp_path / "review.json"), timeout=5, adapter=None, **changes)


def codex_events(*extra):
    return "\n".join(json.dumps(row) for row in [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Check error ordering."}},
        *extra, {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 4}}])


@pytest.mark.parametrize("provider,raw,models", [
    ("codex", codex_events(), []),
    ("claude", json.dumps({"type": "result", "subtype": "success", "result": "Check errors.",
                            "modelUsage": {"claude-exact": {}}, "total_cost_usd": .01}), ["claude-exact"]),
    ("gemini", json.dumps({"response": "Check errors.", "stats": {"models": {"gemini-exact": {}},
                                                                  "tools": {"totalCalls": 0}}}), ["gemini-exact"]),
    ("other", json.dumps({"success": True, "model": "exact", "review": "Check errors."}), ["exact"]),
])
def test_native_and_custom_envelopes_preserve_review_and_identity(forge, provider, raw, models):
    result = forge.provider_output.review_output(provider, raw)
    assert result["review"].startswith("Check error")
    assert result["observed_models"] == models


@pytest.mark.parametrize("provider,raw", [
    ("claude", "[]"), ("claude", '{"type":"result","subtype":"error_max_turns","result":"partial"}'),
    ("claude", '{"type":"result","subtype":"success","result":"x","permission_denials":[{}]}'),
    ("gemini", '{"response":"partial","error":{"message":"failed"}}'),
    ("gemini", '{"response":"text","stats":{"tools":{"totalCalls":1}}}'),
    ("other", '{"review":"pass"}'), ("other", '{"success":true,"review":""}'),
    ("other", '{"success":true,"review":"pass","tool_calls":[{}]}'),
    ("codex", codex_events({"type": "turn.failed"})),
    ("codex", codex_events({"type": "item.completed", "item": {"type": "command_execution"}})),
    ("codex", '{"type":"item.completed","item":{"type":"agent_message","text":"partial"}}'),
])
def test_incomplete_failed_or_tool_using_reviews_fail(forge, provider, raw):
    with pytest.raises(ValueError):
        forge.provider_output.review_output(provider, raw)


@pytest.mark.parametrize("provider", ["codex", "claude", "gemini"])
def test_native_commands_select_exact_model_and_restrict_customization(forge, tmp_path, monkeypatch, provider):
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)
    command = forge.providers.review_command(provider, "exact-model", tmp_path, None)
    assert command[0] == "/native/" + provider
    assert command[command.index("--model") + 1] == "exact-model"
    if provider == "claude":
        assert "--safe-mode" in command and "--bare" not in command
        assert command[command.index("--tools") + 1] == ""
    elif provider == "codex":
        assert "--ignore-user-config" in command and "read-only" in command
        assert "shell_tool" in command and "unified_exec" in command
    else:
        settings = json.loads((tmp_path / ".gemini/settings.json").read_text())
        assert not settings["hooksConfig"]["enabled"]
        assert settings["tools"]["core"] == ["__forge_no_tools__"]
        assert "none" in command and "--skip-trust" in command


def test_auth_status_never_emits_native_account_fields(forge, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)
    calls = []

    def native(command, workspace, **kwargs):
        calls.append(command)
        return {"returncode": 0, "timed_out": False,
                "stdout": '{"loggedIn":true,"email":"private@example.com","token":"secret"}', "stderr": "secret"}

    monkeypatch.setattr(forge.providers, "execute", native)
    assert forge.providers.provider_status(argparse.Namespace(check_auth=False)) == 0
    assert not calls
    capsys.readouterr()
    assert forge.providers.provider_status(argparse.Namespace(check_auth=True)) == 0
    output = capsys.readouterr().out
    assert "secret" not in output and "private@" not in output
    assert len(calls) == 2 and json.loads(output)["providers"][1]["authentication"] == "ready"


def test_failed_auth_process_cannot_report_ready(forge, monkeypatch, capsys):
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: name)
    monkeypatch.setattr(forge.providers, "execute", lambda *a, **kw:
                        {"returncode": 125, "timed_out": False, "stdout": '{"loggedIn":true}', "stderr": ""})
    forge.providers.provider_status(argparse.Namespace(check_auth=True))
    assert all(row["authentication"] != "ready" for row in json.loads(capsys.readouterr().out)["providers"])


def test_missing_binary_is_actionable_without_a_fallback(forge, tmp_path, monkeypatch):
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: None)
    monkeypatch.setattr(forge.providers, "execute", lambda *a, **kw: pytest.fail("Unexpected provider request"))
    options = args(tmp_path)
    assert forge.providers.provider_review(options) == 1
    assert "unavailable: claude" in json.loads(Path(options.output).read_text())["error"]


@pytest.mark.parametrize("change,error", [
    ({"returncode": 124, "timed_out": True}, "exit 124"),
    ({"stdout_truncated": True}, "exceeded"),
    ({"stdout": "not-json"}, "Expecting value"),
    ({"stdout": '{"type":"result","subtype":"success","result":"review","modelUsage":{"other":{}}}'}, "differs"),
])
def test_review_retains_failure_without_fallback(forge, tmp_path, monkeypatch, capsys, change, error):
    options = args(tmp_path); options.model = "selected"
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)
    calls = []

    def native(command, workspace, **kwargs):
        calls.append((command, workspace, kwargs))
        assert not list(workspace.iterdir())
        assert "public errors" in kwargs["prompt"]
        return {"returncode": 0, "timed_out": False, "seconds": .1, "stdout": "", **change}

    monkeypatch.setattr(forge.providers, "execute", native)
    assert forge.providers.provider_review(options) == 1
    report = json.loads(Path(options.output).read_text())
    assert not report["success"] and error in report["error"]
    assert len(calls) == 1 and not calls[0][1].exists()
    assert report["requested_model"] == "selected"


@pytest.mark.parametrize("provider", ["claude", "gemini"])
@pytest.mark.parametrize("selected,models,success,identity", [
    ("selected", ["selected", "fallback"], False, "mismatch_or_alias"),
    ("selected", ["selected"], True, "reported"),
    ("selected", [], True, "unreported"),
    (None, ["selected", "fallback"], True, "reported"),
], ids=["mixed", "exact", "unreported", "default"])
def test_selected_model_requires_every_reported_identity_to_match(forge, tmp_path, monkeypatch,
                                                                 provider, selected, models, success, identity):
    options = args(tmp_path)
    options.provider, options.model = provider, selected
    usage = {model: {} for model in models}
    raw = ({"type": "result", "subtype": "success", "result": "Check error ordering.", "modelUsage": usage}
           if provider == "claude" else {"response": "Check error ordering.", "stats": {"models": usage}})
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)
    calls = []

    def native(command, workspace, **kwargs):
        calls.append(command)
        return {"returncode": 0, "timed_out": False, "seconds": .1, "stdout": json.dumps(raw)}

    monkeypatch.setattr(forge.providers, "execute", native)
    assert forge.providers.provider_review(options) == (0 if success else 1)
    report = json.loads(Path(options.output).read_text())
    assert report["success"] is success and report["model_identity"] == identity
    assert report["requested_model"] == selected and report["observed_models"] == models
    assert report["review"] == "Check error ordering."
    assert len(calls) == 1


@pytest.mark.parametrize("contents", [b"", b"\xff", b"x" * (256 * 1024 + 1)],
                         ids=["empty", "invalid-utf8", "oversized"])
def test_bad_packet_never_calls_provider(forge, tmp_path, monkeypatch, contents):
    options = args(tmp_path)
    Path(options.input).write_bytes(contents)
    monkeypatch.setattr(forge.providers, "execute", lambda *a, **kw: pytest.fail("Unexpected model call"))
    assert forge.providers.provider_review(options) == 1


def test_input_cannot_be_overwritten_by_review(forge, tmp_path):
    options = args(tmp_path); options.output = options.input
    before = Path(options.input).read_bytes()
    assert forge.providers.provider_review(options) == 1
    assert Path(options.input).read_bytes() == before


def test_real_custom_executable_receives_stdin_and_literal_model(tmp_path):
    adapter = tmp_path / "adapter.json"
    script = tmp_path / "reviewer.py"
    script.write_text("import json,sys\nprompt=sys.stdin.read()\n"
                      "assert 'public errors' in prompt\n"
                      "print(json.dumps(dict(success=True,model=sys.argv[1],review='Preserve error ordering.')))\n")
    model = "literal-$model;not-a-shell"
    adapter.write_text(json.dumps({"argv": [sys.executable, str(script), "{model}"]}))
    options = args(tmp_path)
    result = run_raw("provider-review", "--provider", "custom", "--adapter", str(adapter), "--model", model,
                     "--input", options.input, "--output", options.output)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(Path(options.output).read_text())
    assert report["success"] and report["observed_models"] == [model]
    assert report["review"] == "Preserve error ordering."
    assert "review" not in json.loads(result.stdout)


@pytest.mark.parametrize("code,timeout,error", [
    ("import time;time.sleep(20)", .05, "exit 124"),
    ("raise SystemExit(7)", 2, "exit 7"),
    ("print('x' * (1024 * 1024 + 1))", 2, "exceeded"),
])
def test_real_adapter_timeout_nonzero_and_output_limits(tmp_path, code, timeout, error):
    options = args(tmp_path)
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({"argv": [sys.executable, "-c", code]}))
    result = run_raw("provider-review", "--provider", "custom", "--adapter", str(adapter), "--timeout", str(timeout),
                     "--input", options.input, "--output", options.output)
    assert result.returncode == 1 and error in json.loads(Path(options.output).read_text())["error"]
