"""Optional native CLI probes are bounded, secret-free and distinct from model access."""
import argparse
import json

import pytest

from forge_test_helpers import load_zagrosi_module


@pytest.fixture
def status(monkeypatch, capsys):
    forge = load_zagrosi_module()
    monkeypatch.setattr(forge.providers.shutil, "which", lambda name: "/native/" + name)
    calls = []

    def run(result=None, *, check=True, available=True, control=None):
        if not available:
            monkeypatch.setattr(forge.providers.shutil, "which", lambda name: None)

        def execute(argv, workspace, **kwargs):
            calls.append((argv, workspace, kwargs))
            assert "--version" in argv or "--help" in argv
            assert "login" not in argv and "auth" not in argv and not kwargs.get("prompt")
            assert 0 < kwargs["timeout"] <= 10 and kwargs["output_limit"] <= 65536
            assert workspace.is_dir()
            response = {"returncode": 0, "timed_out": False, "seconds": .1, "stderr": "secret"}
            if "--version" in argv:
                response["stdout"] = {"codex": "codex-cli 0.134.0", "claude": "2.1.298 (Claude Code)",
                                      "gemini": "0.39.2"}[argv[0].rsplit("/", 1)[-1]]
            elif "--forge-unsupported-option-check" in argv:
                response.update(stdout="", returncode=2,
                                stderr="error: unknown option '--forge-unsupported-option-check'")
                response.update(control or {})
            else:
                response["stdout"] = "Usage: native [options]\nOptions:\n  --help  Show help\n"
                response.update(result or {})
            return response

        monkeypatch.setattr(forge.providers, "execute", execute)
        assert forge.providers.provider_status(argparse.Namespace(check_auth=False, check_cli=check)) == 0
        raw = capsys.readouterr().out
        assert "secret" not in raw
        report = json.loads(raw)
        assert all(row["authentication"] == "unchecked" for row in report["providers"])
        assert all(not call[1].exists() for call in calls)
        return report["providers"], calls

    return run


def test_default_status_remains_zero_process_and_explicitly_unchecked(status):
    rows, calls = status(check=False)
    assert not calls
    assert all(row["cli"] == {"status": "unchecked", "version": None} for row in rows)


def test_missing_executable_is_unavailable_without_probes(status):
    rows, calls = status(available=False)
    assert not calls
    assert all(row["cli"]["status"] == "unavailable" for row in rows)


def test_help_acceptance_is_not_authentication_and_hidden_flags_do_not_fail(status):
    rows, calls = status()
    assert len(calls) == 9
    assert [row["cli"]["version"] for row in rows] == ["0.134.0", "2.1.298", "0.39.2"]
    assert all(row["cli"]["status"] == "compatible" for row in rows)
    for argv, _, _ in calls:
        if "--help" in argv:
            assert any(flag in argv for flag in ("--ignore-user-config", "--safe-mode", "--skip-trust"))


@pytest.mark.parametrize("control", [
    {"returncode": 0, "stdout": "Usage: native [options]", "stderr": ""},
    {"returncode": 124, "timed_out": True},
    {"stderr_truncated": True},
    {"returncode": 2, "stderr": "unrelated failure"},
])
def test_help_that_does_not_demonstrate_argument_validation_remains_unknown(status, control):
    rows, _ = status(control=control)
    assert all(row["cli"]["status"] == "unknown" for row in rows)


@pytest.mark.parametrize("result", [
    {"returncode": 124, "timed_out": True},
    {"returncode": 125, "termination_error": "Output pipes remain open"},
    {"stdout_truncated": True},
    {"stderr_truncated": True},
    {"returncode": 2, "stderr": "private@example.com secret"},
    {"stdout": '{"result":"This is not help"}'},
])
def test_inconclusive_probes_are_never_compatible(status, result):
    rows, _ = status(result)
    assert all(row["cli"]["status"] == "unknown" for row in rows)


@pytest.mark.parametrize("error", ["error: unknown option '--output-format'", "Unknown argument: output-format",
                                  "error: unexpected argument '--output-format' found"])
def test_explicit_rejected_review_option_is_incompatible(status, error):
    rows, _ = status({"returncode": 2, "stderr": error})
    # Claude/Gemini actually use this flag. Codex must not infer incompatibility from unrelated text.
    assert rows[0]["cli"]["status"] == "unknown"
    assert all(row["cli"]["status"] == "incompatible" for row in rows[1:])
    assert all(row["cli"]["unsupported_flags"] == ["--output-format"] for row in rows[1:])


def test_incomplete_unknown_option_diagnostic_does_not_prove_incompatibility(status):
    rows, _ = status({"returncode": 2, "stderr": "unknown option '--output-format'", "stderr_truncated": True})
    assert all(row["cli"]["status"] == "unknown" for row in rows)
