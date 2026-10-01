"""Invalid saved project sessions fail before changing the planning directory."""

import json
import os

import pytest

from forge_test_helpers import run_raw, write_compact_project_fixture


def snapshot(path):
    return {str(item.relative_to(path)): (os.readlink(item) if item.is_symlink() else
            item.read_bytes() if item.is_file() else None) for item in path.rglob("*")}


def session(planning, directory, content):
    path = planning / directory / "session.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


def command(planning, form):
    if form == "lint":
        return ["lint-project-manifest", "--planning-dir", str(planning), "--strict"]
    if form == "create":
        return ["project-create-dirs", "--planning-dir", str(planning), "--flight", "off"]
    prefix = ["preflight", "--phase", "project"] if form.startswith("preflight") else ["project-setup"]
    inputs = (["--file", str(planning / "requirements.md")] if form.endswith("file") else
              ["--brief", "Resume this project.", "--planning-dir", str(planning)])
    return [*prefix, *inputs, "--flight", "off"]


@pytest.mark.parametrize("directory", [".zagrosi-project", ".deep-project"])
@pytest.mark.parametrize("content", [b"{ invalid", b"[]", b"null"])
@pytest.mark.parametrize("form", ["brief", "file", "preflight-brief", "preflight-file", "lint", "create"])
def test_invalid_authoritative_session_fails_without_mutation(tmp_path, directory, content, form):
    (tmp_path / "requirements.md").write_text("REQ-001: Preserve the original brief.\n")
    (tmp_path / "project-manifest.md").write_text("<!-- SPLIT_MANIFEST\n01-core\nEND_MANIFEST -->\n")
    path = session(tmp_path, directory, content)
    if directory == ".zagrosi-project":
        session(tmp_path, ".deep-project", json.dumps({"initial_file": str(tmp_path / "requirements.md")}).encode())
    before = snapshot(tmp_path)

    result = run_raw(*command(tmp_path, form))

    assert result.returncode == 1 and not result.stderr, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    message = payload.get("error") or payload["findings"][0]["message"]
    assert "Invalid project session" in message and str(path) in message
    if form == "lint":
        assert payload["findings"][0]["code"] == "invalid-project-session"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("content", [b'"session"', b"42", b"true", b"\xff"])
def test_other_invalid_session_values_are_structured_failures(tmp_path, content):
    session(tmp_path, ".zagrosi-project", content)
    before = snapshot(tmp_path)
    result = run_raw(*command(tmp_path, "brief"))
    assert result.returncode == 1 and not result.stderr
    assert "Invalid project session" in json.loads(result.stdout)["error"]
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("kind", ["directory", "dangling-link"])
def test_unreadable_current_session_cannot_fall_back_to_legacy(tmp_path, kind):
    session(tmp_path, ".deep-project", b"{}")
    path = tmp_path / ".zagrosi-project" / "session.json"
    path.parent.mkdir()
    if kind == "directory":
        path.mkdir()
    else:
        try:
            path.symlink_to("missing-session.json")
        except OSError:
            pytest.skip("Symlinks are unavailable")
    before = snapshot(tmp_path)
    result = run_raw(*command(tmp_path, "brief"))
    assert result.returncode == 1 and not result.stderr
    assert str(path) in json.loads(result.stdout)["error"]
    assert snapshot(tmp_path) == before


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are unavailable")
@pytest.mark.parametrize("directory", [".zagrosi-project", ".deep-project"])
def test_nonregular_session_is_rejected_without_blocking(tmp_path, directory):
    (tmp_path / "requirements.md").write_text("REQ-001: Preserve the brief.\n")
    if directory == ".deep-project":
        session(tmp_path, ".zagrosi-project", json.dumps({"initial_file": str(tmp_path / "requirements.md")}).encode())
    path = tmp_path / directory / "session.json"
    path.parent.mkdir()
    os.mkfifo(path)
    before = snapshot(tmp_path)
    result = run_raw(*command(tmp_path, "brief"), timeout=5)
    assert not result.stderr
    payload = json.loads(result.stdout)
    assert payload["success"] is (directory == ".deep-project")
    assert result.returncode == (0 if payload["success"] else 1)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("legacy", [b"{ invalid", b"[]", b"null"])
def test_valid_current_session_ignores_invalid_legacy_and_preserves_files(tmp_path, legacy):
    planning = write_compact_project_fixture(tmp_path)
    session(planning, ".zagrosi-project", json.dumps({
        "initial_file": str(planning / "requirements.md"), "depth_mode": "lean", "future_field": {"keep": True},
    }).encode())
    session(planning, ".deep-project", legacy)
    before = snapshot(planning)
    for form in ("brief", "preflight-file", "lint"):
        result = run_raw(*command(planning, form))
        assert result.returncode == 0, result.stderr + result.stdout
        assert json.loads(result.stdout)["success"]
    assert snapshot(planning) == before


@pytest.mark.parametrize("initial", [None, "missing.md"])
def test_valid_legacy_source_fallback_keeps_current_session_authority(tmp_path, initial):
    planning = write_compact_project_fixture(tmp_path)
    legacy_source = planning / "legacy.md"
    legacy_source.write_bytes((planning / "requirements.md").read_bytes())
    current = session(planning, ".zagrosi-project", json.dumps({"initial_file": initial, "depth_mode": "lean"}).encode())
    session(planning, ".deep-project", json.dumps({"initial_file": str(legacy_source), "depth_mode": "deep"}).encode())
    before = snapshot(planning)
    result = run_raw(*command(planning, "brief"))
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["mode"] == "resume" and payload["depth_mode"] == "lean"
    assert payload["state_dir"] == str(current.parent)
    assert payload["initial_file"] == str(legacy_source)
    assert payload["generated_requirements_file"] is None
    result = run_raw(*command(planning, "lint"))
    assert result.returncode == 0, result.stderr + result.stdout
    assert snapshot(planning) == before


@pytest.mark.parametrize("directory", [".zagrosi-project", ".deep-project"])
def test_explicit_file_override_preserves_valid_saved_session(tmp_path, directory):
    planning = write_compact_project_fixture(tmp_path)
    original = planning / "original.md"
    original.write_text("REQ-001: Original saved input.\n")
    session(planning, directory, json.dumps({"initial_file": str(original), "depth_mode": "standard"}).encode())
    before = snapshot(planning)
    result = run_raw(*command(planning, "file"))
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["initial_file"] == str(planning / "requirements.md")
    assert any("Session was created for" in warning for warning in payload["warnings"])
    assert snapshot(planning) == before


def test_invalid_session_is_reported_before_missing_manifest(tmp_path):
    session(tmp_path, ".zagrosi-project", b"[]")
    result = run_raw(*command(tmp_path, "lint"))
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout)["findings"][0]["code"] == "invalid-project-session"
