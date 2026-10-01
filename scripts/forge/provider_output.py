"""Normalize native CLI envelopes without interpreting a review as an approval."""
from __future__ import annotations

import json


def optional_object(value, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {label}")
    return value


def codex_output(raw: str) -> dict:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise ValueError("Expected Codex JSON event objects")
    if any(event.get("type") in {"error", "turn.failed"} for event in events):
        raise ValueError("Codex reported a failed turn")
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if len(completed) != 1:
        raise ValueError("Expected one completed Codex turn")
    pending, finished, text = {}, set(), []
    started = ended = seen_item = False
    for event in events:
        kind, item = event.get("type"), event.get("item")
        if isinstance(item, dict) and item.get("type") not in {"agent_message", "reasoning"}:
            raise ValueError("Packet review attempted tool activity")
        if kind == "turn.started":
            if started or ended or seen_item:
                raise ValueError("Invalid Codex turn lifecycle")
            started = True
        elif kind == "turn.completed":
            if pending:
                raise ValueError("Codex returned unfinished review items")
            ended = True
        elif kind in {"item.started", "item.updated", "item.completed"}:
            if ended or not isinstance(item, dict):
                raise ValueError("Invalid Codex item lifecycle")
            seen_item = True
            identity = item.get("id")
            if identity is not None and (not isinstance(identity, str) or not identity or identity in finished):
                raise ValueError("Invalid Codex item identity")
            if identity in pending and pending[identity] != item["type"]:
                raise ValueError("Codex item type changed during review")
            if kind != "item.completed":
                if identity is None or (kind == "item.started" and identity in pending):
                    raise ValueError("Invalid Codex item lifecycle")
                pending[identity] = item["type"]
            else:
                if identity is not None:
                    pending.pop(identity, None)
                    finished.add(identity)
                if item.get("type") == "agent_message":
                    if not isinstance(item.get("text"), str):
                        raise ValueError("Invalid completed Codex review text")
                    text.append(item["text"])
    models = list(dict.fromkeys(event["model"] for event in events if isinstance(event.get("model"), str)))
    return {"review": "\n\n".join(text), "observed_models": models, "usage": completed[0].get("usage")}


def review_output(provider: str, raw: str) -> dict:
    if provider == "codex":
        result = codex_output(raw)
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
            models = optional_object(data.get("modelUsage"), "Claude model usage")
            result = {"review": data.get("result"),
                      "observed_models": list(models),
                      "usage": data.get("usage"), "cost_usd": data.get("total_cost_usd")}
        elif provider == "gemini":
            stats = optional_object(data.get("stats"), "Gemini statistics")
            tool_stats = optional_object(stats.get("tools"), "Gemini tool statistics")
            calls = tool_stats.get("totalCalls")
            if calls is not None and type(calls) not in {int, float}:
                raise ValueError("Invalid Gemini tool call count")
            if calls:
                raise ValueError("Packet review attempted tool activity")
            models = optional_object(stats.get("models"), "Gemini model statistics")
            result = {"review": data.get("response"),
                      "observed_models": list(models), "usage": stats}
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
