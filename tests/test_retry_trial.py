"""The protected oracle rejects independently broken retry semantics."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def fixed_source(text):
    return text.replace('key = event["id"]', 'key = (event["tenant"], event["id"])').replace(
        '        completed.add(key)\n        send(event["tenant"], event["topic"], deepcopy(event["payload"]))',
        '        send(event["tenant"], event["topic"], deepcopy(event["payload"]))\n        completed.add(key)')


@pytest.mark.parametrize("mutation", ["baseline", "valid", "global-id", "premature", "payload-alias", "clear-progress", "partial-validation"])
def test_oracle_preserves_retries_isolation_and_side_effect_order(tmp_path, mutation):
    workspace = tmp_path / "workspace"
    shutil.copytree(ROOT / "examples/evals/coding/retry-queue", workspace)
    source = workspace / "src/dispatch.py"
    text = source.read_text()
    if mutation != "baseline":
        text = fixed_source(text)
    if mutation == "global-id":
        text = text.replace('key = (event["tenant"], event["id"])', 'key = event["id"]')
    elif mutation == "premature":
        text = text.replace('        completed.add(key)\n', '').replace('        send(', '        completed.add(key)\n        send(')
    elif mutation == "payload-alias":
        text = text.replace('deepcopy(event["payload"])', 'event["payload"]')
    elif mutation == "clear-progress":
        text = text.replace('    validate(events)', '    completed.clear()\n    validate(events)')
    elif mutation == "partial-validation":
        text = text.replace('    validate(events)\n', '').replace('    for event in events:\n', '    for event in events:\n        validate([event])\n')
    source.write_text(text)
    # Writer-controlled tests cannot replace this external acceptance check.
    (workspace / "tests/test_dispatch.py").write_text('')
    command = [sys.executable, "-B", str(ROOT / "tools/retry_trial_checks.py"), str(workspace), "retry-queue"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert (result.returncode == 0) is (mutation == "valid"), result.stderr
    if mutation == "valid":
        assert json.loads(result.stdout) == {"case": "retry-queue", "assertions": 271}
