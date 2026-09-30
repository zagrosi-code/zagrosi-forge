"""Independent state and behavior oracles for the existing first-task example."""
from __future__ import annotations

import importlib
import importlib.util
import hashlib
from functools import cache
import json
from pathlib import Path
import sys

from coding_trial_evidence import files
from coding_trial_process import execute
from native_trial_session import PROGRESS, read_json

TESTS = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
ORACLE = '''
import inspect
from labels import normalize
assert list(inspect.signature(normalize).parameters) == ['value']
parameter = inspect.signature(normalize).parameters['value']
assert parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
assert parameter.default is inspect.Parameter.empty
assert normalize(value=' Ada ') == 'Ada'
for value, expected in [('', ''), ('  ', ''), (' \\tAda  Lovelace\\n', 'Ada  Lovelace'),
                        ('MiXeD', 'MiXeD'), ('\\u2003X \\tY\\u2003', 'X \\tY')]:
    assert normalize(value) == expected
class Probe:
    def strip(self): raise AssertionError('called strip before checking type')
    def __str__(self): raise AssertionError('coerced a non-string')
for value in [None, 0, [], Probe()]:
    try: normalize(value)
    except TypeError as error: assert str(error) == 'label must be a string'
    else: raise AssertionError('accepted a non-string')
print('first-task oracle passed')
'''


@cache
def loaded_runtime(root):
    namespace = "_native_trial_" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(namespace, root / "scripts/zagrosi_skills.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    return launcher.load_runtime()


def runtime_module(root, name):
    runtime = loaded_runtime(root)
    return importlib.import_module(runtime.MODULE_NAMES[f"forge/{name}.py"])


def admission(root, workspace, depth):
    result = execute([sys.executable, "-B", str(root / "scripts/zagrosi_skills.py"), "postflight", "--phase", "plan",
                      "--planning-dir", str(workspace / ".planning"), "--depth", depth, "--strict"], workspace)
    try:
        payload = json.loads(result["stdout"])
    except ValueError:
        payload = {}
    shape = compact_section(root, workspace)
    return {"success": result["returncode"] == 0 and payload.get("success") is True and payload.get("depth_mode") == depth and shape["success"],
            "process": result, "report": payload, "section_shape": shape}


def compact_section(root, workspace):
    planning = workspace / ".planning"
    sections = runtime_module(root, "sections").check_section_progress(planning).get("sections", [])
    compact = runtime_module(root, "artifacts").compact_plan_descriptor(planning)
    return {"success": len(sections) == 1 and bool(compact) and not compact["errors"],
            "section_count": len(sections), "compact": bool(compact)}


def changes(baseline, workspace):
    current = files(workspace)
    return sorted(name for name in current.keys() | baseline.keys() if current.get(name) != baseline.get(name))


def expected_red(workspace):
    result = execute([*TESTS, "-p", "test_labels.py"], workspace)
    text = result["stderr"]
    success = (result["returncode"] != 0 and "FAIL: test_surrounding_whitespace_only" in text
               and "FAILED (failures=1)" in text and "ERROR:" not in text and "skipped=" not in text)
    return {"success": success, "process": result}


def planned(root, workspace, depth, baseline):
    changed = changes(baseline, workspace)
    outside = [name for name in changed if not name.startswith(".planning/")]
    ready = admission(root, workspace, depth)
    red = expected_red(workspace)
    return {"success": ready["success"] and not outside and red["success"],
            "admission": ready, "outside_planning": outside, "baseline_red": red}


def interrupted(root, workspace, depth, baseline, session):
    event = session.get("checkpoint")
    try:
        current = runtime_module(root, "mutable_inputs").contract_snapshot(workspace / ".planning", event["section"], target_dir=workspace)
        fresh = event.get("snapshot") == current
    except (KeyError, TypeError, ValueError, OSError):
        fresh = False
    red = expected_red(workspace)
    changed = changes(baseline, workspace)
    outside = [name for name in changed if not name.startswith(".planning/")
               and not (name.startswith("tests/") and name not in baseline)]
    ready = admission(root, workspace, depth)
    return {"success": session.get("stop") == "checkpoint" and session.get("returncode") not in (None, 0) and not session.get("termination_error")
            and not any(session.get(key) for key in ("reader_error", "terminal_error", "invalid_events", "output_exceeded"))
            and fresh and ready["success"] and red["success"] and not outside,
            "snapshot_fresh": fresh, "checkpoint": event, "workspace_sha256": files(workspace),
            "admission": ready, "baseline_red": red, "outside_planning": outside}


def completed(root, workspace, depth, baseline, checkpoint):
    result = execute([sys.executable, "-B", str(Path(__file__).with_name("coding_trial_evidence.py")),
                      str(root), str(workspace), depth], workspace)
    try:
        workflow = json.loads(result["stdout"])
    except ValueError:
        workflow = {}
    tests, oracle = execute(TESTS, workspace), execute([sys.executable, "-B", "-c", ORACLE], workspace)
    events = read_json(workspace / PROGRESS, {}).get("events", [])
    receipt = runtime_module(root, "verification").integration_report(workspace / ".planning", workspace)
    shape = compact_section(root, workspace)
    altered = changes(baseline, workspace)
    protected = [name for name in altered if name in baseline and name != "labels.py"]
    outside = [name for name in altered if name != "labels.py" and not name.startswith(("tests/", ".planning/"))]
    return {"success": result["returncode"] == 0 and workflow.get("success") is True
            and workflow.get("sections_recorded_complete") is True and tests["returncode"] == oracle["returncode"] == 0
            and checkpoint in events and receipt["success"] and receipt["source"] == "captured" and not protected and not outside and shape["success"],
            "workflow": workflow, "workflow_process": result, "tests": tests, "behavior_oracle": oracle,
            "receipt": receipt, "checkpoint_preserved": checkpoint in events, "protected_changes": protected,
            "outside_scope": outside, "changed_files": altered, "section_shape": shape}
