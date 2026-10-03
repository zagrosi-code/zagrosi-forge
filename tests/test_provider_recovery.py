"""Provider failures explain the next safe action without inventing auth problems."""
import argparse
import json

import pytest

from forge_test_helpers import load_zagrosi_module


@pytest.fixture
def review(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    source, output = tmp_path / "packet.md", tmp_path / "review.json"
    source.write_text("Review the public API without changing its behavior.")
    options = argparse.Namespace(provider="claude", model="selected", input=str(source),
                                 output=str(output), timeout=5, adapter=None)
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)
    calls = []

    def run(**changes):
        def execute(*args, **kwargs):
            calls.append((args, kwargs))
            return {"returncode": 0, "timed_out": False, "seconds": .1,
                    "stdout": '{"type":"result","subtype":"success","result":"Review.","modelUsage":{"selected":{}}}',
                    "stderr": "private@example.com secret", **changes}

        monkeypatch.setattr(forge.providers, "execute", execute)
        assert forge.providers.provider_review(options) == 1
        report = json.loads(output.read_text())
        summary = json.loads(capsys.readouterr().out)
        assert len(calls) == 1
        assert report["requested_model"] == "selected"
        assert report["input_sha256"] == summary["input_sha256"]
        assert report["failure_kind"] == summary["failure_kind"]
        assert report["recovery"] and not report["success"]
        assert "secret" not in output.read_text() and "private@" not in output.read_text()
        assert "login" not in report["recovery"].lower()
        assert "login_argv" not in report
        return report

    return run


@pytest.mark.parametrize("changes,kind,message", [
    ({"returncode": 124, "timed_out": True}, "timeout", "exit 124"),
    ({"returncode": 7}, "request_failed", "exit 7"),
    ({"stdout_truncated": True}, "output_limit", "exceeded"),
    ({"stdout": "not-json"}, "invalid_output", "Expecting value"),
    ({"stdout": '{"type":"result","subtype":"success","result":"Review.","modelUsage":{"other":{}}}'},
     "model_mismatch", "differs"),
])
def test_failures_keep_specific_recovery_without_auth_advice(review, changes, kind, message):
    report = review(**changes)
    assert report["failure_kind"] == kind and message in report["error"]
    if kind == "timeout":
        assert "5" in report["error"] and "timeout" in report["recovery"].lower()


@pytest.mark.parametrize("returncode,timed_out", [(125, False), (124, True), (0, False)])
def test_cleanup_failure_is_never_a_success_or_login_problem(review, returncode, timed_out):
    report = review(returncode=returncode, timed_out=timed_out,
                    termination_error="Output pipes remain open; detached descendants may have escaped cleanup")
    assert report["failure_kind"] == "process_cleanup"
    assert "cleanup" in report["error"].lower()
    assert "descendant" in report["termination_error"]
    assert report["timed_out"] is timed_out


def test_cleanup_diagnostic_has_a_safe_bound(review):
    report = review(returncode=125, termination_error="\x1b[31m" + "x" * 2000 + "\r\n")
    assert len(report["termination_error"]) <= 512
    assert all(character.isprintable() for character in report["termination_error"])


def test_missing_executable_has_install_recovery_without_process(tmp_path, monkeypatch, capsys):
    forge = load_zagrosi_module()
    source, output = tmp_path / "packet.md", tmp_path / "review.json"
    source.write_text("Review this packet.")
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: None)
    monkeypatch.setattr(forge.providers, "execute", lambda *a, **k: pytest.fail("Unexpected process"))
    options = argparse.Namespace(provider="claude", model=None, input=str(source), output=str(output),
                                 timeout=5, adapter=None)
    assert forge.providers.provider_review(options) == 1
    report = json.loads(output.read_text())
    assert report["failure_kind"] == "executable_unavailable"
    assert "unavailable: claude" in report["error"]
    assert "install" in report["recovery"].lower()


def test_bad_input_retains_validation_failure_without_process(tmp_path, monkeypatch):
    forge = load_zagrosi_module()
    monkeypatch.setattr(forge.providers, "execute", lambda *a, **k: pytest.fail("Unexpected process"))
    output = tmp_path / "review.json"
    options = argparse.Namespace(provider="claude", model=None, input=str(tmp_path / "missing.md"),
                                 output=str(output), timeout=5, adapter=None)
    assert forge.providers.provider_review(options) == 1
    assert json.loads(output.read_text())["failure_kind"] == "invalid_request"
