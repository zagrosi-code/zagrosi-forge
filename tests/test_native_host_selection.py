"""Single-host native staging and matrices through fake CLIs; no model calls or user settings."""
import json
import os
from pathlib import Path
import sys

import pytest

from test_native_workflow_trials import native, trials

MANIFEST = ".codex-plugin/package-files.json"
SKILLS = ("zagrosi-cleanup", "zagrosi-forge", "zagrosi-implement", "zagrosi-plan", "zagrosi-project")
FAKE_CLAUDE = r'''
import json, os, shutil, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_CLI_LOG"], "a") as log:
    log.write(json.dumps(["claude", *args]) + "\n")
if args == ["--version"]:
    sys.exit(print("2.1.304 (Claude Code)"))
config = Path(os.environ["CLAUDE_CONFIG_DIR"])
state = config / "plugins/state.json"
data = json.loads(state.read_text()) if state.exists() else {"marketplaces": {}, "installed": []}
mode = os.environ.get("FAKE_CLAUDE_MODE")
if args[:2] == ["plugin", "validate"]:
    pass
elif args[:3] == ["plugin", "marketplace", "add"]:
    data["marketplaces"][json.loads(Path(args[3], ".claude-plugin/marketplace.json").read_text())["name"]] = args[3]
elif args[:2] == ["plugin", "install"]:
    name, market = args[2].split("@", 1)
    root = Path(data["marketplaces"][market])
    entry = next(row for row in json.loads((root / ".claude-plugin/marketplace.json").read_text())["plugins"] if row["name"] == name)
    version = json.loads((root / entry["source"] / ".claude-plugin/plugin.json").read_text())["version"]
    target = (config.parent / "escaped" if mode == "escape" else config / "plugins/cache") / market / name / version
    shutil.copytree(root / entry["source"], target)
    if mode == "corrupt":
        (target / "README.md").write_text("changed")
    data["installed"].append({"id": args[2], "version": version, "enabled": True, "installPath": str(target)})
elif args == ["plugin", "list", "--json"]:
    sys.exit(print(json.dumps(data["installed"])))
elif args[:2] == ["plugin", "details"]:
    row = next(row for row in data["installed"] if args[2] in {row["id"], row["id"].split("@")[0]})
    names = sorted(path.parent.name for path in Path(row["installPath"]).glob("skills/*/SKILL.md"))
    names = names[1:] if mode == "missing-skill" else names
    sys.exit(print(f"Component inventory\n  Skills ({len(names)})  {', '.join(names)}\n  Agents (0)"))
else:
    sys.exit(f"unsupported {args}")
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps(data))
'''
FAKE_CODEX = r'''
import json, os, shutil, sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_CLI_LOG"], "a") as log:
    log.write(json.dumps(["codex", *args]) + "\n")
if not os.environ.get("FAKE_CODEX_ALLOWED"):
    sys.exit(97)
home = Path(os.environ["CODEX_HOME"])
state = home / "state.json"
markets = json.loads(state.read_text()) if state.exists() else {}
if args[:3] == ["plugin", "marketplace", "add"]:
    markets[json.loads(Path(args[3], ".agents/plugins/marketplace.json").read_text())["name"]] = args[3]
    state.write_text(json.dumps(markets))
elif args[:2] == ["plugin", "add"]:
    name, market = args[2].split("@", 1)
    target = home / "plugins/cache" / market / name / "local"
    shutil.copytree(Path(markets[market], "plugin"), target)
    print(json.dumps({"installedPath": str(target)}))
else:
    sys.exit(f"unsupported {args}")
'''


def package(root):
    content = {MANIFEST: "", "README.md": "package bytes\n", ".codex-plugin/plugin.json": "{}\n",
               ".claude-plugin/plugin.json": json.dumps({"name": "zagrosi-forge", "version": "9.9.9"}),
               "scripts/zagrosi_skills.py": "# launcher\n", **{f"skills/{name}/SKILL.md": f"# {name}\n" for name in SKILLS}}
    for name, text in content.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / MANIFEST).write_text(json.dumps(sorted(content)))
    return root


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Shadow both native CLIs; the fake Codex fails unless a test explicitly selects Codex."""
    if os.name == "nt":
        pytest.skip("Fake CLIs use POSIX executable scripts")
    bin_dir, log = tmp_path / "bin", tmp_path / "cli.jsonl"
    bin_dir.mkdir()
    log.touch()
    for name, source in {"claude": FAKE_CLAUDE, "codex": FAKE_CODEX}.items():
        (bin_dir / f"{name}.py").write_text(source)
        path = bin_dir / name
        # A shell wrapper keeps interpreter paths with spaces working.
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir / name}.py" "$@"\n')
        path.chmod(0o755)
    user = tmp_path / "user-config"
    user.mkdir()
    (user / "settings.json").write_text("{}")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user))
    return lambda: [json.loads(line) for line in log.read_text().splitlines()]


def test_claude_only_staging_installs_natively_without_codex(tmp_path, fake_cli):
    source = package(tmp_path / "source")
    staging = tmp_path / "staging"
    result = native.prepare_live(source, staging, hosts=("claude",))
    installed, settings = Path(result["claude_plugin"]).resolve(), Path(result["claude_settings"]).resolve()
    assert result["hosts"] == ["claude"] and not {"codex_plugin", "temporary_cache"} & result.keys()
    assert installed.is_relative_to(settings) and settings.is_relative_to(staging.resolve())
    assert native.mismatches(source, installed, json.loads((source / MANIFEST).read_text())) == []
    assert [call[:3] for call in fake_cli()] == [["claude", "plugin", "validate"], ["claude", "plugin", "marketplace"],
                                                 ["claude", "plugin", "install"], ["claude", "plugin", "list"]]
    user = tmp_path / "user-config"
    assert [path.name for path in user.iterdir()] == ["settings.json"] and (user / "settings.json").read_text() == "{}"


def test_two_host_staging_keeps_each_catalog_separate(tmp_path, fake_cli, monkeypatch):
    # Codex also reads .claude-plugin catalogs, so its marketplace must contain only its own.
    monkeypatch.setenv("FAKE_CODEX_ALLOWED", "1")
    source = package(tmp_path / "source")
    result = native.prepare_live(source, tmp_path / "staging", codex_home=tmp_path / "codex-home", hosts=("codex", "claude"))
    codex_marketplace, claude_marketplace = Path(result["marketplace"]), Path(result["claude_marketplace"])
    assert (codex_marketplace / ".agents/plugins/marketplace.json").is_file() and not (codex_marketplace / ".claude-plugin").exists()
    assert (claude_marketplace / ".claude-plugin/marketplace.json").is_file() and not (claude_marketplace / ".agents").exists()
    members = json.loads((source / MANIFEST).read_text())
    assert native.mismatches(source, Path(result["codex_plugin"]), members) == []
    assert native.mismatches(source, Path(result["claude_plugin"]), members) == []
    assert result["hosts"] == ["codex", "claude"] and Path(result["temporary_cache"]).is_relative_to(tmp_path / "codex-home")


@pytest.mark.parametrize("mode", ["corrupt", "escape"])
def test_claude_install_rejects_mismatched_or_escaped_copies(tmp_path, fake_cli, monkeypatch, mode):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(ValueError, match="does not match"):
        native.prepare_live(package(tmp_path / "source"), tmp_path / "staging", hosts=("claude",))


def test_single_host_matrix_continues_on_the_same_host():
    rows = trials.matrix(("claude",))
    assert [row["id"] for row in rows] == [row["id"] for row in trials.matrix() if row["host"] == "claude"]
    assert len(rows) == 7 and all(row.get("resume_host", "claude") == row["host"] == "claude" for row in rows)
    assert all(row["resume_host"] == "codex" for row in trials.matrix() if row["host"] == "claude" and row["trigger"] == "direct")


def test_claude_only_prepare_records_only_selected_host(tmp_path, fake_cli):
    root = package(tmp_path / "plugin")
    record = trials.prepare(tmp_path / "matrix", root, {"codex": "unused", "claude": "pinned"}, {"claude": "max"}, hosts=("claude",))
    assert record["hosts"] == ["claude"] and record["models"] == {"claude": "pinned"} and record["efforts"] == {"claude": "max"}
    assert record["claude_discovery"] == list(SKILLS) and "codex_discovery" not in record and "codex_configuration" not in record
    assert record["cli_versions"] == {"claude": "2.1.304 (Claude Code)"} and record["planned_model_calls"] == 19
    assert {row["host"] for row in record["rows"]} == {"claude"}
    assert all((tmp_path / "matrix" / row["id"] / "baseline.json").is_file() for row in record["rows"])
    assert trials.package_unchanged(record, {"claude"}) and not [call for call in fake_cli() if call[0] == "codex"]


def test_prepare_rejects_unsupported_effort_before_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(trials, "prepare_live", lambda *args, **kwargs: pytest.fail("staging must not start"))
    for hosts, efforts, error in ((("codex",), {"codex": "max"}, "codex does not accept 'max'"),
                                  (("gemini",), {}, "codex, claude or both"), ((), {}, "codex, claude or both")):
        with pytest.raises(ValueError, match=error):
            trials.prepare(tmp_path / "matrix", package(tmp_path / "plugin"), {}, efforts, hosts=hosts)
    assert not (tmp_path / "matrix").exists()


def test_prepare_cli_selects_one_host(tmp_path, monkeypatch, capsys):
    captured = {}

    def prepare(directory, root, models, efforts, **kwargs):
        captured.update(efforts=efforts, **kwargs)
        return {"success": True}

    monkeypatch.setattr(trials, "prepare", prepare)
    monkeypatch.setattr(sys, "argv", ["native_workflow_trials.py", "prepare", str(tmp_path / "matrix"),
                                      "--host", "claude", "--claude-model", "pinned", "--effort", "max"])
    assert trials.main() == 0 and json.loads(capsys.readouterr().out) == {"success": True}
    assert captured["hosts"] == ("claude",) and captured["efforts"] == {"claude": "max"}


def test_claude_failure_never_strands_a_codex_namespace(tmp_path, fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_ALLOWED", "1")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "corrupt")
    with pytest.raises(ValueError, match="does not match"):
        native.prepare_live(package(tmp_path / "source"), tmp_path / "staging", codex_home=tmp_path / "codex-home",
                            hosts=("codex", "claude"))
    assert not (tmp_path / "codex-home").exists() and not [call for call in fake_cli() if call[0] == "codex"]


def test_failed_discovery_removes_the_codex_namespace(tmp_path, fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_ALLOWED", "1")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "missing-skill")
    staged = {}

    def stage(*args, **kwargs):
        staged.update(native.prepare_live(*args, codex_home=tmp_path / "codex-home", **kwargs))
        return staged

    monkeypatch.setattr(trials, "prepare_live", stage)
    monkeypatch.setattr(trials, "codex_skills", lambda *args, **kwargs: [
        {"name": "zagrosi-forge:" + name, "path": str(Path(staged["codex_plugin"], "skills", name, "SKILL.md")), "enabled": True}
        for name in SKILLS])
    with pytest.raises(ValueError, match="Native Claude did not register"):
        trials.prepare(tmp_path / "matrix", package(tmp_path / "plugin"), {"codex": "pinned", "claude": "pinned"},
                       {"codex": "medium", "claude": "medium"})
    cache = Path(staged["temporary_cache"])
    assert cache.parent.is_dir() and not cache.exists()


def test_single_host_matrix_rejects_unprepared_hosts_without_losing_rows(tmp_path, fake_cli, monkeypatch, capsys):
    directory = tmp_path / "matrix"
    trials.prepare(directory, package(tmp_path / "plugin"), {"claude": "pinned"}, {"claude": "medium"}, hosts=("claude",))
    with pytest.raises(ValueError, match="codex was not prepared"):
        trials.run(directory, "claude-lean-direct", resume_host="codex")
    with pytest.raises(ValueError, match="codex was not prepared"):
        trials.report(directory, "codex")
    for flags in (["--host", "codex"], ["--case", "claude-lean-direct", "--codex-model", "other"],
                  ["--case", "claude-lean-direct", "--resume-host", "codex"]):
        monkeypatch.setattr(sys, "argv", ["native_workflow_trials.py", "run", str(directory), *flags])
        with pytest.raises(SystemExit) as stopped:
            trials.main()
        assert stopped.value.code == 1 and "codex was not prepared" in capsys.readouterr().err
    assert trials.read_json(directory / "matrix.json")["models"] == {"claude": "pinned"}
    assert not (directory / "claude-lean-direct/result.json").exists()


def test_claude_smoke_installs_and_discovers_through_shared_helpers(fake_cli):
    from forge_test_helpers import ROOT
    result = native.smoke(ROOT, "claude", "claude")
    assert result["success"], result
    assert result["discovered_skills"] == result["expected_skills"] == list(SKILLS)
