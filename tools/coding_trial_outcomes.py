"""Shared code-review criteria and honest cost-per-accepted accounting."""
from __future__ import annotations

import math

CRITERIA = {
    "readability": "Can an engineer follow the main behavior, names, and error paths directly?",
    "cohesion": "Do module/helper boundaries own coherent responsibilities without speculative layers?",
    "duplication": "Are shared causes removed without hiding distinct behavior behind flags or wrappers?",
    "regressions": "Do meaningful tests protect behavior, compatibility, and changed boundaries?",
}


def accepted_outcomes(attempts: list[dict], *, status_key: str = "status") -> dict:
    """Charge every scheduled attempt to accepted work; unknown is never free."""
    accepted = sum(row.get(status_key) == "passed" for row in attempts)
    def aggregate(values):
        valid = [value for value in values if type(value) in (int, float) and math.isfinite(value) and value >= 0]
        total = sum(valid) if len(valid) == len(values) and values else None
        return {"observed": sum(valid) if valid else None, "observed_attempts": len(valid),
                "total": total, "per_accepted": total / accepted if total is not None and accepted else None}
    totals = {key: aggregate([((row.get("reported_telemetry") or {}).get("totals") or {}).get(key)
                              for row in attempts])
              for key in ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")}
    return {"accepted": accepted, "scheduled": len(attempts),
            "accepted_rate": accepted / len(attempts) if attempts else None,
            "elapsed_seconds": aggregate([row.get("attempt_seconds") for row in attempts]),
            "tokens": totals,
            "reported_cost_usd": aggregate([(row.get("reported_telemetry") or {}).get("reported_cost_usd") for row in attempts]),
            "interventions": aggregate([(row.get("reported_telemetry") or {}).get("interventions") for row in attempts]),
            "limits": "Every attempt counts, including failures. Missing observations leave totals unknown; zero accepted leaves per-accepted values undefined. Elapsed time excludes independent review. CLI cost estimates are not subscription bills."}
