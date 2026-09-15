from __future__ import annotations

import json
import os
import signal
from pathlib import Path

import pytest
from detached_test_support import (
    copy_implementation_plugin,
    file_sha256,
    implementation_source_args,
    make_detached_record_fixture,
    write_test_admission_pinner,
)
from fault_injection_support import (
    instrument_root_lifecycle_points,
)
from forge_test_helpers import (
    run_script_raw,
    write_single_section_fixture,
)


@pytest.mark.parametrize(
    "crashpoint",
    (
        "setup-slot-temp-fsync:zagrosi_implement_config.json",
        "canonical-temp-fsync:zagrosi_implement_config.json",
    ),
)
def test_detached_setup_real_sigkill_recovers_fixed_root_temps(
    tmp_path: Path,
    crashpoint: str,
) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / crashpoint.split(":", 1)[0])
    script = instrument_root_lifecycle_points(plugin_root)
    planning = tmp_path / "planning"
    sections = write_single_section_fixture(planning)
    target = tmp_path / "target"
    target.mkdir()
    implementation_root = tmp_path / "detached-implementation"
    admission_pinner = write_test_admission_pinner(
        tmp_path / "admission-pinner.json",
        planning_dir=planning,
    )
    arguments = (
        "implement-setup",
        "--sections-dir",
        str(sections),
        "--target-dir",
        str(target),
        "--implementation-root",
        str(implementation_root),
        "--admission-pinner",
        str(admission_pinner),
        "--expected-admission-pinner-sha256",
        file_sha256(admission_pinner),
        *implementation_source_args(plugin_root),
        "--flight",
        "off",
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_ROOT_CRASHPOINT"] = crashpoint
    crashed = run_script_raw(script, *arguments, env=environment)
    assert crashed.returncode == -signal.SIGKILL
    assert any(path.name.endswith(".tmp") for path in implementation_root.iterdir())

    recovered = run_script_raw(script, *arguments)
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert {path.name for path in implementation_root.iterdir()} == {
        "code_review",
        "evidence",
        "pinners",
        "zagrosi_implement_config.json",
        "zagrosi_implement_state.json",
        "forge-progress.json",
    }
    assert not any(path.name.endswith(".tmp") for path in implementation_root.iterdir())


def test_detached_progress_real_sigkill_recovers_without_stale_lock_or_duplicate_event(tmp_path: Path) -> None:
    plugin_root = copy_implementation_plugin(tmp_path / "progress-temp")
    script = instrument_root_lifecycle_points(plugin_root)
    fixture = make_detached_record_fixture(tmp_path / "fixture-progress-temp", plugin_root=plugin_root)
    arguments = (
        "implement-progress",
        "--planning-dir",
        str(fixture.planning),
        "--implementation-root",
        str(fixture.implementation_root),
        "--section",
        fixture.section,
        "--stage",
        "red",
        "--result",
        "expected failure",
    )
    environment = dict(os.environ)
    environment["ZAGROSI_TEST_ROOT_CRASHPOINT"] = "canonical-temp-fsync:forge-progress.json"
    crashed = run_script_raw(script, *arguments, env=environment)
    assert crashed.returncode == -signal.SIGKILL
    assert (fixture.implementation_root / ".forge-progress.json.tmp").is_file()
    assert not (fixture.implementation_root / ".forge-progress.json.lock").exists()

    recovered = run_script_raw(script, *arguments)
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    payload = json.loads(recovered.stdout)
    assert payload["event_count"] == 1
    assert not (fixture.implementation_root / ".forge-progress.json.tmp").exists()
    assert not (fixture.implementation_root / ".forge-progress.json.lock").exists()
