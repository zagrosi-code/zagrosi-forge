"""Normalize native CLI envelopes without interpreting a review as an approval."""
from __future__ import annotations

import json


def review_output(provider: str, raw: str) -> dict:
    if provider == "codex":
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if not events or any(not isinstance(item, dict) for item in events):
            raise ValueError("Expected Codex JSON event objects")
        if any(item.get("type") in {"error", "turn.failed"} for item in events):
            raise ValueError("Codex reported a failed turn")
        completed = [item for item in events if item.get("type") == "turn.completed"]
        if len(completed) != 1:
            raise ValueError("Expected one completed Codex turn")
        items = [event["item"] for event in events if isinstance(event.get("item"), dict)]
        if any(item.get("type") not in {"agent_message", "reasoning"} for item in items):
            raise ValueError("Packet review attempted tool activity")
        text = "\n\n".join(item["text"] for item in items
                            if item.get("type") == "agent_message" and isinstance(item.get("text"), str))
        models = list(dict.fromkeys(event["model"] for event in events if isinstance(event.get("model"), str)))
        result = {"review": text, "observed_models": models, "usage": completed[0].get("usage")}
    else:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        if data.get("error") or data.get("is_error") or data.get("success") is False:
            raise ValueError("Provider reported an unsuccessful request")
        if provider == "claude":
            if data.get("type") != "result" or data.get("subtype") != "success":
                raise ValueError("Expected a successful Claude result")
            if data.get("permission_denials"):
                raise ValueError("Packet review attempted denied tool activity")
            models = data.get("modelUsage", {})
            result = {"review": data.get("result"),
                      "observed_models": list(models) if isinstance(models, dict) else [],
                      "usage": data.get("usage"), "cost_usd": data.get("total_cost_usd")}
        elif provider == "gemini":
            stats = data.get("stats") or {}
            if not isinstance(stats, dict):
                raise ValueError("Invalid Gemini statistics")
            tool_stats = stats.get("tools") or {}
            if isinstance(tool_stats, dict) and tool_stats.get("totalCalls", 0):
                raise ValueError("Packet review attempted tool activity")
            models = stats.get("models") or {}
            result = {"review": data.get("response"),
                      "observed_models": list(models) if isinstance(models, dict) else [], "usage": stats}
        else:
            if data.get("success") is not True:
                raise ValueError("Adapter must report success explicitly")
            if data.get("tool_calls"):
                raise ValueError("Packet review attempted tool activity")
            model = data.get("model")
            result = {"review": data.get("review"), "observed_models": [model] if isinstance(model, str) else [],
                      "usage": data.get("usage"), "cost_usd": data.get("cost_usd")}
    if not isinstance(result["review"], str) or not result["review"].strip():
        raise ValueError("Provider returned no review text")
    return result
