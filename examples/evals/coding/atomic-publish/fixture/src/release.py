"""Public release command, also imported by our deployment script."""
from publisher import BundlePublisher, publish_text


def publish_release(directory, version, notes, on_publish=None):
    written = publish_text(directory, [("version.txt", version), ("notes.txt", notes)], on_publish)
    return {"version": version, "written": written}


def publish_assets(directory, assets, on_publish=None):
    return BundlePublisher(directory).write(assets, on_publish)
