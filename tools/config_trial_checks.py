"""External layered-configuration oracle: prompt contracts plus legacy observations."""
from copy import deepcopy
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

FIXTURE = Path(__file__).resolve().parents[1] / "examples/evals/coding/config-layers/fixture"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parameters(call):
    return [(name, value.kind, value.default) for name, value in inspect.signature(call).parameters.items()]


def outcome(call):
    try:
        return "return", call()
    except Exception as exc:
        return "raise", type(exc).__name__, exc.args


def traced(function, layers, environment):
    events = []

    class Layer(dict):
        def items(self):
            for key, value in super().items():
                events.append(("item", key))
                yield key, value

    class Environment(dict):
        def __contains__(self, key):
            events.append(("contains", key))
            return super().__contains__(key)

        def __getitem__(self, key):
            events.append(("get", key))
            return super().__getitem__(key)

    def once():
        for name, data in layers:
            events.append(("layer", name))
            yield name, Layer(data) if isinstance(data, dict) else data

    result = outcome(lambda: function(once(), Environment(environment)))
    return result, events


def verify(workspace: Path) -> int:
    sys.path.insert(0, str(workspace / "src"))
    candidate = load(workspace / "src/settings.py", "settings")
    sys.modules["settings"] = candidate
    caller = load(workspace / "src/service.py", "candidate_service")
    baseline = load(FIXTURE / "src/settings.py", "baseline_settings")
    count = 0

    def check(condition, message):
        nonlocal count
        assert condition, message
        count += 1

    # Existing API and caller behavior are compared to the frozen fixture.
    for name in ("load_config", "settings_text", "ConfigReader"):
        check(parameters(getattr(candidate, name)) == parameters(getattr(baseline, name)),
              "Changed signature: " + name)
    for name in ("snapshot", "render"):
        check(parameters(getattr(candidate.ConfigReader, name))
              == parameters(getattr(baseline.ConfigReader, name)), "Changed reader signature")
    check(candidate.DEFAULTS == baseline.DEFAULTS, "Changed public defaults")
    check(set(baseline.__all__) <= set(candidate.__all__), "Removed public exports")
    check(callable(getattr(candidate, "explain_config", None)), "Missing explain_config feature")
    check("explain_config" in candidate.__all__, "New API missing from __all__")
    check(parameters(candidate.explain_config) == parameters(baseline.load_config), "Wrong explain_config signature")

    valid = [[], [("file", {})], [("base", {"port": 8123, "tags": ["one", "two"]})],
             [("same", {"host": "host", "debug": True}), ("same", {"host": None})],
             [("base", {"tags": ["one"]}), ("local", {"tags": []})]]
    environments = [{}, {"OTHER": "ignored"}, {"APP_PORT": "0081", "APP_DEBUG": "true"},
                    {"APP_HOST": "host", "APP_TAGS": "blue,green"}, {"APP_TAGS": ""}]
    for layers in valid:
        for env in environments:
            for name in ("load_config", "settings_text"):
                expected = traced(getattr(baseline, name), deepcopy(layers), env)
                check(traced(getattr(candidate, name), deepcopy(layers), env) == expected,
                      "Legacy values/access order changed: " + name)
            expected = baseline.load_config(layers, env)
            check(candidate.ConfigReader(deepcopy(layers), env).snapshot() == expected, "Reader values changed")
            check(candidate.ConfigReader(deepcopy(layers), env).render() == baseline.settings_text(layers, env),
                  "Reader formatting changed")
            check(caller.bind_address(deepcopy(layers), env) == expected["host"] + ":" + str(expected["port"]),
                  "Launcher caller changed")
            check(caller.support_report(deepcopy(layers), env) == {
                "settings": expected, "text": baseline.settings_text(layers, env)}, "Support caller changed")
    malformed = [[("", {"other": 1})], [("x", [])], [("x", {"other": 1, "port": False})],
                 [("x", {"port": False, "other": 1})], [("x", {"tags": [""]})],
                 [("x", {"host": ""})], [("x", {"port": 0})], [("x", {"port": 65536})],
                 [("x", {"debug": 1})], [("x", {"tags": "x"})],
                 [("good", {"port": 9000}), ("bad", {"port": "9000"}), ("unread", {})]]
    invalid_env = [{"APP_HOST": "", "APP_PORT": "bad"}, {"APP_PORT": "1.5"},
                   {"APP_DEBUG": "TRUE"}, {"APP_TAGS": "a,,b"}, {"APP_PORT": False},
                   {"APP_PORT": "0"}, {"APP_PORT": "65536"}, {"APP_HOST": None}]
    for layers, env in [(layers, {"APP_PORT": "bad"}) for layers in malformed] + [([], env) for env in invalid_env]:
        for name in ("load_config", "settings_text", "explain_config"):
            expected = traced(baseline.load_config if name == "explain_config" else getattr(baseline, name), layers, env)
            check(traced(getattr(candidate, name), layers, env) == expected, "Validation/order changed: " + name)

    # Fixed provenance expectations are independent of any implementation helper.
    defaults = {"host": "127.0.0.1", "port": 8000, "debug": False, "tags": []}
    default_sources = {key: {"kind": "default", "name": None} for key in defaults}
    scenarios = [([], {}, defaults, default_sources)]
    for key, value in (("host", "api"), ("port", 4321), ("debug", True), ("tags", ["a"])):
        for assigned in (value, defaults[key], None):
            values = deepcopy(defaults)
            values[key] = deepcopy(defaults[key] if assigned is None else assigned)
            sources = deepcopy(default_sources)
            sources[key] = {"kind": "layer", "name": "final"}
            scenarios.append(([("base", {key: value}), ("final", {key: assigned})], {}, values, sources))
    scenarios.append(([("same", {"host": "a", "port": 9000}), ("same", {"port": 9000})],
                      {"APP_PORT": "9000", "APP_TAGS": "x,y", "UNUSED": object()},
                      {"host": "a", "port": 9000, "debug": False, "tags": ["x", "y"]},
                      {"host": {"kind": "layer", "name": "same"},
                       "port": {"kind": "environment", "name": "APP_PORT"},
                       "debug": {"kind": "default", "name": None},
                       "tags": {"kind": "environment", "name": "APP_TAGS"}}))
    scenarios.append(([], {"APP_HOST": "127.0.0.1", "APP_DEBUG": "false", "APP_TAGS": ""},
                      defaults, {"host": {"kind": "environment", "name": "APP_HOST"},
                                 "port": {"kind": "default", "name": None},
                                 "debug": {"kind": "environment", "name": "APP_DEBUG"},
                                 "tags": {"kind": "environment", "name": "APP_TAGS"}}))
    for layers, env, values, sources in scenarios:
        original = deepcopy(layers)
        result = candidate.explain_config((layer for layer in layers), env)
        check(result == {"values": values, "sources": sources}, "Incorrect values/provenance")
        check(list(result["values"]) == list(defaults) and list(result["sources"]) == list(defaults),
              "Settings/source order changed")
        check(layers == original, "Input layers mutated")
        check(len({id(value) for value in result["sources"].values()}) == 4, "Source records alias")
        expected_events = traced(baseline.load_config, original, env)[1]
        check(traced(candidate.explain_config, original, env)[1] == expected_events, "Feature input access order changed")
        result["values"]["tags"].append("mutation")
        result["sources"]["host"]["kind"] = "mutation"
        check(layers == original and candidate.DEFAULTS == defaults, "Result shares mutable inputs/defaults")
        check(candidate.explain_config(deepcopy(layers), env) == {"values": values, "sources": sources},
              "Results share mutable state")
    with patch.dict(os.environ, {"APP_HOST": "ambient", "APP_PORT": "4321", "APP_DEBUG": "true", "APP_TAGS": "ambient"}):
        check(candidate.explain_config([]) == {"values": defaults, "sources": default_sources}, "Omitted environment changed")
        check(candidate.load_config([]) == defaults, "Loader read ambient environment")
        check(candidate.settings_text([]) == baseline.settings_text([]), "Renderer read ambient environment")
        check(candidate.ConfigReader([]).snapshot() == defaults, "Reader read ambient environment")
        check(candidate.ConfigReader([]).render() == baseline.settings_text([]), "Reader renderer read ambient environment")
    return count


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]))}))
