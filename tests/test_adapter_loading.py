"""Project-specific privileged policy loads only for its integration."""

import json
import sys

from forge_test_helpers import SCRIPT
from runtime_support import load_runtime


def test_ordinary_status_does_not_load_privileged_adapter(tmp_path, capsys):
    runtime = load_runtime(SCRIPT)
    assert runtime.entrypoint.main(["status", "--path", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["success"]
    prefix = runtime.package.__name__ + "."
    loaded = {name.removeprefix(prefix) for name in sys.modules if name.startswith(prefix)}
    assert not loaded & {"unit12_adapter", "handoff", "handoff_host"}
    assert "HANDOFF_ROOT" not in vars(runtime.detached_contract)


def test_legacy_handoff_contract_access_resolves_trusted_adapter():
    runtime = load_runtime(SCRIPT)
    assert runtime.detached_contract.HANDOFF_SECTION_CONTRACTS is runtime.unit12_adapter.HANDOFF_SECTION_CONTRACTS
    assert runtime.detached_contract.HANDOFF_GIT == runtime.unit12_adapter.HANDOFF_GIT
