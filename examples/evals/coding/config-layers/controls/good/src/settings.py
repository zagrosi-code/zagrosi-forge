"""Service settings assembled from ordered files and explicit environment values."""
from copy import deepcopy

__all__ = ["DEFAULTS", "load_config", "settings_text", "ConfigReader", "explain_config"]
DEFAULTS = {"host": "127.0.0.1", "port": 8000, "debug": False, "tags": []}


def _value(key, value):
    if value is None:
        return deepcopy(DEFAULTS[key])
    valid = ((key == "host" and isinstance(value, str) and bool(value))
             or (key == "port" and type(value) is int and 1 <= value <= 65535)
             or (key == "debug" and type(value) is bool)
             or (key == "tags" and isinstance(value, list)
                 and all(isinstance(tag, str) and tag for tag in value)))
    if not valid:
        raise ValueError("Invalid setting: " + key)
    return deepcopy(value)


def _environment(key, raw):
    if not isinstance(raw, str):
        raise ValueError("Invalid APP_" + key.upper())
    if key == "host":
        value = raw
    elif key == "port":
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("Invalid APP_PORT") from None
    elif key == "debug":
        if raw not in ("true", "false"):
            raise ValueError("Invalid APP_DEBUG")
        value = raw == "true"
    else:
        value = [] if raw == "" else raw.split(",")
    try:
        return _value(key, value)
    except ValueError:
        raise ValueError("Invalid APP_" + key.upper()) from None


def _resolve(layers, environ):
    values = deepcopy(DEFAULTS)
    sources = {key: {"kind": "default", "name": None} for key in DEFAULTS}
    for name, data in layers:
        if not isinstance(name, str) or not name:
            raise ValueError("Invalid layer name")
        if not isinstance(data, dict):
            raise ValueError("Layer must be an object: " + name)
        for key, value in data.items():
            if key not in DEFAULTS:
                raise ValueError("Unknown setting: " + str(key))
            values[key] = _value(key, value)
            sources[key] = {"kind": "layer", "name": name}
    if environ is not None:
        for key in DEFAULTS:
            variable = "APP_" + key.upper()
            if variable in environ:
                values[key] = _environment(key, environ[variable])
                sources[key] = {"kind": "environment", "name": variable}
    return {"values": values, "sources": sources}


def load_config(layers, environ=None):
    return _resolve(layers, environ)["values"]


def settings_text(layers, environ=None):
    values = load_config(layers, environ)
    return "\n".join(key + "=" + str(value) for key, value in values.items())


def explain_config(layers, environ=None):
    return _resolve(layers, environ)


class ConfigReader:
    def __init__(self, layers, environ=None):
        self.layers = layers
        self.environ = environ

    def snapshot(self):
        return load_config(self.layers, self.environ)

    def render(self):
        return settings_text(self.layers, self.environ)
