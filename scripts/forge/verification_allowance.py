"""Prospective accounting for helper-owned checks, under the mutable workflow lock."""

from pathlib import Path
import sys

from . import storage


def checked_path(planning: Path) -> Path:
    from .verification import _check_managed_path

    path = planning / "implementation" / "verification" / "allowance.json"
    _check_managed_path(planning, path, single_link=True)
    return path


def _seconds(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= sys.float_info.max


class Allowance:
    """No state means unlimited. Pending allocation is uncertainty, not elapsed time."""

    def __init__(self, planning: Path, target: Path, total=None):
        self.planning = planning
        self.timeout = None
        self.changed = False
        if total is not None and not _seconds(total):
            raise ValueError("Verification allowance must be finite, nonnegative seconds.")
        path = checked_path(planning)
        identity = {"version": 1, "planning_dir": str(planning.resolve()), "target_dir": str(target.resolve())}
        exists = path.exists()
        self.state = storage.load_json(path) if exists else None
        if exists:
            state = self.state
            if (not isinstance(state, dict) or type(state.get("version")) is not int
                    or any(state.get(key) != value for key, value in identity.items())
                    or not all(_seconds(state.get(key)) for key in (
                        "limit_seconds", "measured_seconds", "unmeasured_seconds", "pending_seconds"))
                    or not _seconds(self.spent)):
                raise ValueError("Malformed or mismatched verification allowance; preserve it for inspection.")
            if total is not None and total < state["limit_seconds"]:
                raise ValueError("Verification allowance cannot decrease or reset; supply a higher total to extend it.")
            if total is not None and total > state["limit_seconds"]:
                self.state = {**state, "limit_seconds": total, "pending_seconds": 0,
                              "unmeasured_seconds": state["unmeasured_seconds"] + state["pending_seconds"]}
                self.changed = True
        elif total is not None:
            self.state = {**identity, "limit_seconds": total, "measured_seconds": 0,
                          "unmeasured_seconds": 0, "pending_seconds": 0}
            self.changed = True

    @property
    def spent(self):
        return sum(self.state[key] for key in ("measured_seconds", "unmeasured_seconds", "pending_seconds"))

    def _save(self, state):
        storage.write_json(checked_path(self.planning), state)
        self.state, self.changed = state, False

    def reserve(self, timeout):
        if self.state is None:
            return timeout
        state = self.state
        remaining = max(0, state["limit_seconds"] - self.spent)
        if state["pending_seconds"] or not remaining:
            if self.changed:
                self._save(state)  # An explicit zero total must survive the refused launch.
            reason = "has unresolved execution accounting" if state["pending_seconds"] else "is exhausted"
            raise ValueError(
                f"Verification allowance {reason}: total={state['limit_seconds']}, "
                f"measured={state['measured_seconds']}, unmeasured={state['unmeasured_seconds']}, "
                f"pending={state['pending_seconds']}, remaining={remaining} seconds. "
                "Check not run; required verification remains outstanding unless already satisfied by fresh evidence. "
                "Explicit continuation requires --allowance-seconds with a higher total; prior charges remain."
            )
        self.timeout = float(min(timeout, remaining))
        self._save({**state, "pending_seconds": self.timeout})
        return self.timeout

    def settle(self, result):
        if self.state is None:
            return
        seconds = result.get("seconds")
        if not _seconds(seconds) or not _seconds(self.state["measured_seconds"] + self.state["unmeasured_seconds"] + seconds):
            raise ValueError("Verification has no valid elapsed measurement; allocation remains pending. "
                             "Explicit continuation requires --allowance-seconds with a higher total.")
        self._save({**self.state, "measured_seconds": self.state["measured_seconds"] + seconds,
                    "pending_seconds": 0})

    def report(self):
        if self.state is None:
            return {}
        return {"allowance": {key: value for key, value in self.state.items() if key.endswith("_seconds")} | {
            "remaining_seconds": max(0, self.state["limit_seconds"] - self.spent),
            "effective_timeout_seconds": self.timeout,
        }}
