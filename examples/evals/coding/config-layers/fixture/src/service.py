"""Existing callers used by the service launcher and its support command."""
from settings import ConfigReader, load_config, settings_text


def bind_address(layers, environ=None):
    config = load_config(layers, environ)
    return config["host"] + ":" + str(config["port"])


def support_report(layers, environ=None):
    return {"settings": ConfigReader(layers, environ).snapshot(),
            "text": settings_text(layers, environ)}
