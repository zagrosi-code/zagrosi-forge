"""Publish the files of a small release bundle to an existing directory."""
from pathlib import Path

__all__ = ["publish_files", "publish_text", "BundlePublisher"]


def _collect(directory, documents):
    prepared = []
    names = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Document must be an object")
        name = document.get("name")
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name or "\x00" in name):
            raise ValueError("Invalid document name")
        data = document.get("data")
        if not isinstance(data, bytes):
            raise ValueError("Invalid document data: " + name)
        if name in names:
            raise ValueError("Duplicate document: " + name)
        names.add(name)
        prepared.append((name, data))
    root = Path(directory)
    if not root.is_dir():
        raise ValueError("Output directory does not exist")
    for name, _ in prepared:
        target = root / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("Target is not a regular file: " + name)
    return root, prepared


def publish_files(directory, documents, on_publish=None):
    root, prepared = _collect(directory, documents)
    written = []
    for name, data in prepared:
        target = root / name
        target.write_bytes(data)
        written.append(name)
        if on_publish is not None:
            on_publish(name, target)
    return written


def publish_text(directory, documents, on_publish=None):
    encoded = [{"name": name, "data": text.encode("utf-8")}
               for name, text in documents]
    return publish_files(directory, encoded, on_publish)


class BundlePublisher:
    def __init__(self, directory):
        self.directory = directory

    def write(self, documents, on_publish=None):
        root, prepared = _collect(self.directory, documents)
        written = []
        for name, data in prepared:
            target = root / name
            target.write_bytes(data)
            written.append(name)
            if on_publish is not None:
                on_publish(name, target)
        return written
