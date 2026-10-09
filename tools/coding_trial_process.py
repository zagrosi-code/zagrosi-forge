"""Compatibility wrapper around Forge's shared bounded subprocess executor."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("forge_trial_process_launcher", ROOT / "scripts/zagrosi_skills.py")
_launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_launcher)
_runtime = _launcher.load_runtime()
_execute = importlib.import_module(_runtime.MODULE_NAMES["forge/child_process.py"]).execute


def execute(argv: list[str], workspace: Path, *, prompt: str | None = None, timeout: float = 60,
            env: dict[str, str] | None = None, output_limit: int = 12000,
            inherit_env: bool = True) -> dict:
    if env is None:
        env = {"PYTHONPATH": str(workspace / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": "0"}
    return _execute(argv, workspace, prompt=prompt, timeout=timeout, env=env,
                    output_limit=output_limit, inherit_env=inherit_env)
