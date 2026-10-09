"""Independent optional-entry controls; authored without execution.

Static native fixtures are fictional. Preparation tests stop before the first
controller call and cannot qualify or launch any native host.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import stat
import subprocess

import pytest

from test_trial_suite_native_runtime import (
    SELECTED_ENTRY, byte_hash, forbid_credential_reads, make_native, persist, read,
)
from trial_suite_fixtures import digest, link, make_suite, regular_entries
from trial_suite_prepare_cases import PYTHON, bytes_at, save_manifest, schedule
from coding_trial_manifest import read_suite, validate_suite
import coding_trial_suite as preparation


MISSING = "<omitted>"


@pytest.fixture(autouse=True)
def forbid_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Entry-selection controls must not start a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def suite_case(tmp_path, purpose):
    path, suite = make_suite(tmp_path / "curator")
    if purpose == "prospective":
        native = make_native(tmp_path / "profile-shape")
        suite["purpose"] = purpose
        suite["host"] = deepcopy(native["suite"]["host"])
        (path.parent / "profile.json").write_bytes((native["suite_root"] / "profile.json").read_bytes())
        for arm in suite["arms"].values():
            product = arm["product"]
            if product is not None:
                product.update(source_commit="b" * 40, source_tree="c" * 40)
            arm["loading"] = {"adapter": "codex-plain-v1" if product is None else "codex-plugin-v1",
                              "receipt": None}
    return path, suite


def selection(arm, value):
    if value == MISSING:
        arm["loading"].pop("selected_entry", None)
    else:
        arm["loading"]["selected_entry"] = value


@pytest.mark.parametrize("purpose", ["synthetic", "prospective"])
@pytest.mark.parametrize("arm_id", ["alpha", "bravo", "charlie"])
@pytest.mark.parametrize("chosen", [MISSING, None, "SKILL.md"])
def test_selected_entry_manifest_contract(tmp_path, purpose, arm_id, chosen):
    path, suite = suite_case(tmp_path, purpose)
    arm = suite["arms"][arm_id]
    selection(arm, chosen)
    # Opaque configuration and the external prompt cannot become entry selectors.
    arm["configuration"]["selected_entry"] = "not-the-declared-choice.md"
    save_manifest(path, suite)
    before, raw = deepcopy(suite), path.read_bytes()
    valid = chosen in (MISSING, None) or (purpose == "prospective" and arm["product"] is not None)
    if valid:
        validated = validate_suite(suite, path.parent)
        assert validated == read_suite(path) == before
        assert digest(validated) == digest(before)
        assert ("selected_entry" in validated["arms"][arm_id]["loading"]) == (chosen != MISSING)
        validated["arms"][arm_id]["loading"]["receipt"] = "detached-only.json"
    else:
        with pytest.raises(ValueError, match="^suite-invalid:"):
            validate_suite(suite, path.parent)
        with pytest.raises(ValueError, match="^suite-invalid:"):
            read_suite(path)
    assert suite == before and path.read_bytes() == raw


@pytest.mark.parametrize("chosen", [
    "", "/SKILL.md", "C:/SKILL.md", "C:\\SKILL.md", "docs\\entry.md", ".", "./SKILL.md",
    "docs/../SKILL.md", "docs//entry.md", "SKILL.md/", "missing.md", "only-directory",
    "entry\0.md", False, 7, [], {},
])
def test_selected_entry_rejects_noncanonical_or_nonfile_resources(tmp_path, chosen):
    path, suite = suite_case(tmp_path, "prospective")
    arm = suite["arms"]["bravo"]
    source = path.parent / arm["product"]["payload"]
    (source / "only-directory").mkdir()
    arm["product"]["inventory_sha256"] = digest(regular_entries(
        source, ("SKILL.md", "plugin.json", "only-directory")))
    selection(arm, chosen)
    before, files = deepcopy(suite), bytes_at(path.parent)
    with pytest.raises(ValueError, match="^suite-invalid:"):
        validate_suite(suite, path.parent)
    assert suite == before and bytes_at(path.parent) == files


def test_selected_entry_preserves_unrelated_unknown_key_rejection(tmp_path):
    path, suite = suite_case(tmp_path, "prospective")
    suite["arms"]["charlie"]["loading"].update(selected_entry="SKILL.md", guessed_entry="SKILL.md")
    before = deepcopy(suite)
    with pytest.raises(ValueError, match="^suite-invalid:"):
        validate_suite(suite, path.parent)
    assert suite == before


@pytest.mark.parametrize("kind", ["file", "directory", "dangling", "escaping"])
def test_selected_entry_reuses_product_alias_policy(tmp_path, kind):
    path, suite = suite_case(tmp_path, "prospective")
    arm = suite["arms"]["charlie"]
    source = path.parent / arm["product"]["payload"]
    (source / "docs").mkdir()
    outside = tmp_path / "outside-entry.md"
    outside.write_text("Outside the product payload.\n")
    target = {"file": "SKILL.md", "directory": "docs", "dangling": "absent.md",
              "escaping": os.path.relpath(outside, source).replace(os.sep, "/")}[kind]
    alias = source / "selected-link.md"
    link(alias, target, directory=kind == "directory")
    entries = regular_entries(source, ("SKILL.md", "plugin.json", "docs"))
    entries[alias.name] = {"type": "symlink", "mode": stat.S_IMODE(alias.lstat().st_mode), "target": target}
    arm["product"]["inventory_sha256"] = digest(entries)
    selection(arm, alias.name)
    before = deepcopy(suite)
    if kind == "file":
        assert validate_suite(suite, path.parent) == before
    else:
        with pytest.raises(ValueError, match="^suite-invalid:"):
            validate_suite(suite, path.parent)
    assert suite == before and os.readlink(alias) == target
    assert outside.read_text() == "Outside the product payload.\n"


def add_equivalent_native_entry(fixture):
    alternate = "docs/equivalent entry λ.md"
    runtime, native = fixture["runtime"], fixture["native"]
    cache = runtime["installed"]["path"].removeprefix("/codex/plugins/")
    raw = (fixture["source"] / SELECTED_ENTRY).read_bytes()
    for root in (fixture["source"], Path(fixture["roots"]["product"]),
                 native / "plugins" / cache, native / "marketplace/payload"):
        (root / alternate).write_bytes(raw)
    product_hash = digest(regular_entries(fixture["source"], (
        ".codex-plugin/plugin.json", SELECTED_ENTRY, "scripts/helper.py", alternate)))
    fixture["suite"]["arms"][fixture["arm"]]["product"]["inventory_sha256"] = product_hash
    runtime["product_sha256"] = runtime["installed"]["inventory_sha256"] = product_hash
    runtime["plugins_sha256"] = digest(regular_entries(
        native / "plugins", (*fixture["plugin_names"], cache + "/" + alternate)))
    runtime["marketplace_sha256"] = digest(regular_entries(
        native / "marketplace", (*fixture["marketplace_names"], "payload/" + alternate)))
    persist(fixture)
    return alternate


@pytest.mark.parametrize("choice", ["omitted", "matching", "different-path", "null", "alternate-matching"])
def test_declared_native_entry_matches_exact_runtime_path(tmp_path, choice):
    fixture = make_native(tmp_path, configuration={"selected_entry": "opaque-data-only.md"})
    alternate = add_equivalent_native_entry(fixture)
    arm = fixture["suite"]["arms"][fixture["arm"]]
    declared = {"omitted": MISSING, "matching": SELECTED_ENTRY, "different-path": alternate,
                "null": None, "alternate-matching": alternate}[choice]
    selection(arm, declared)
    if choice == "alternate-matching":
        fixture["runtime"]["selected_entry"] = alternate
        persist(fixture)
    before, files = deepcopy(fixture["suite"]), bytes_at(tmp_path)
    assert (fixture["source"] / alternate).read_bytes() == (fixture["source"] / SELECTED_ENTRY).read_bytes()
    if choice in {"different-path", "null"}:
        with pytest.raises(ValueError, match="^loading-unqualified:"):
            read(fixture)
    else:
        result = read(fixture)
        assert result == fixture["runtime"]
        assert result["entry_sha256"] == byte_hash(fixture["suite_root"] / arm["entry"])
    assert fixture["suite"] == before and bytes_at(tmp_path) == files


@pytest.mark.parametrize("chosen", [MISSING, None])
def test_plain_native_runtime_accepts_omitted_or_explicit_null_selection(tmp_path, chosen):
    fixture = make_native(tmp_path, plain=True)
    selection(fixture["suite"]["arms"][fixture["arm"]], chosen)
    assert read(fixture) == fixture["runtime"]
    assert fixture["runtime"]["selected_entry"] is None


@pytest.mark.parametrize("operation", ["prepare", "run", "shared-study"])
@pytest.mark.parametrize("chosen", [MISSING, None])
def test_missing_native_selection_precedes_study_creation(tmp_path, monkeypatch, operation, chosen):
    path, suite = suite_case(tmp_path, "prospective")
    selection(suite["arms"]["bravo"], chosen)
    selection(suite["arms"]["charlie"], "SKILL.md")
    suite["host"]["credentials"] = "codex-native-auth"
    save_manifest(path, suite)
    before = bytes_at(path.parent)
    trial = tmp_path / "new-trial"
    study = trial.with_name(trial.name + ".study")
    auth = tmp_path / "fictional-auth.json"
    auth.write_text('{"fictional":"must never be read"}\n')
    forbid_credential_reads(monkeypatch, auth)
    def forbidden(*args, **kwargs):
        pytest.fail("Missing scheduled entry must fail before the controller or native setup")
    monkeypatch.setattr(preparation, "_controller", forbidden)
    with pytest.raises(ValueError, match="^suite-invalid:") as failure:
        if operation == "shared-study":
            # The missing arm is after both a plain row and a complete product row.
            preparation._freeze_study(study, path, schedule("alpha", "charlie", "bravo"), PYTHON)
        elif operation == "prepare":
            preparation.prepare_suite(trial, path, "normalize", "bravo", assessor_python=PYTHON)
        else:
            preparation.run_suite(trial, path, "normalize", "bravo", assessor_python=PYTHON, auth_file=auth)
    assert "selected" in str(failure.value).lower() and "entry" in str(failure.value).lower()
    assert not trial.exists() and not study.exists() and bytes_at(path.parent) == before


@pytest.mark.parametrize("purpose,arm_id,chosen", [
    ("synthetic", "bravo", MISSING), ("synthetic", "charlie", None),
    ("prospective", "alpha", MISSING), ("prospective", "alpha", None),
    ("prospective", "bravo", "SKILL.md"),
])
def test_eligible_schedules_reach_controller_without_requiring_unscheduled_entries(
        tmp_path, monkeypatch, purpose, arm_id, chosen):
    path, suite = suite_case(tmp_path, purpose)
    selection(suite["arms"][arm_id], chosen)
    save_manifest(path, suite)
    destination = tmp_path / "fresh-study"
    class ControllerReached(Exception):
        pass
    def stop_before_process(*args, **kwargs):
        assert not destination.exists()
        raise ControllerReached
    monkeypatch.setattr(preparation, "_controller", stop_before_process)
    with pytest.raises(ControllerReached):
        preparation._freeze_study(destination, path, schedule(arm_id), PYTHON)
    assert not destination.exists()
