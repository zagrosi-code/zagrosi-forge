"""External bundle oracle: documented transaction boundaries and legacy API."""
from copy import deepcopy
import builtins
from contextlib import ExitStack
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import stat
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

FIXTURE = Path(__file__).resolve().parents[1] / "examples/evals/coding/atomic-publish/fixture"


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


def inventory(root):
    return {item.name: (item.read_bytes(), stat.S_IMODE(item.stat().st_mode))
            for item in root.iterdir() if item.is_file()}


def setup(root, size, existing):
    for index in range(size):
        if index in existing:
            target = root / f"file-{index}"
            target.write_bytes(f"old-{index}".encode())
            target.chmod(0o640 if index % 2 else 0o600)
    (root / "untouched").write_bytes(b"leave alone")
    return [{"name": f"file-{index}", "data": f"new-{index}-caf\u00e9".encode()}
            for index in range(size)]


def verify(workspace: Path) -> int:
    sys.path.insert(0, str(workspace / "src"))
    # Imports may legally bind aliases before the scenario injects failures. A
    # forwarding callable preserves those aliases without requiring import style.
    def forward(module, name):
        original = getattr(module, name)

        def call(*args, **kwargs):
            current = getattr(module, name)
            return (original if current is call else current)(*args, **kwargs)

        return call

    with ExitStack() as importing:
        for module, name in ((os, "replace"), (os, "open"), (builtins, "open"), (io, "open")):
            importing.enter_context(patch.object(module, name, forward(module, name)))
        candidate = load(workspace / "src/publisher.py", "publisher")
        sys.modules["publisher"] = candidate
        caller = load(workspace / "src/release.py", "candidate_release")
    baseline = load(FIXTURE / "src/publisher.py", "baseline_publisher")
    count = 0

    def check(condition, message):
        nonlocal count
        assert condition, message
        count += 1

    for name in ("publish_files", "publish_text", "BundlePublisher"):
        check(parameters(getattr(candidate, name)) == parameters(getattr(baseline, name)),
              "Changed public signature: " + name)
    check(parameters(candidate.BundlePublisher.write) == parameters(baseline.BundlePublisher.write),
          "Changed BundlePublisher.write signature")
    check(set(baseline.__all__) <= set(candidate.__all__), "Removed public exports")
    entrypoints = [candidate.publish_files,
                   lambda root, documents, callback: candidate.BundlePublisher(root).write(documents, callback),
                   caller.publish_assets]
    real_replace = os.replace
    # Success: input order, detached input, staging before replacement, atomic swaps,
    # callback visibility, preexisting permissions and complete cleanup.
    for publish in entrypoints:
        for size in (0, 1, 3):
            with TemporaryDirectory() as folder:
                root = Path(folder)
                documents = setup(root, size, set(range(size)))
                original = deepcopy(documents)
                before = inventory(root)
                callbacks, replacements, consumed = [], [], []

                def once():
                    for document in documents:
                        consumed.append(document["name"])
                        yield document

                def replace(source, destination, *args, **kwargs):
                    target = Path(destination)
                    if target.name in {doc["name"] for doc in original}:
                        check(Path(source).parent == root and Path(source) != target, "Replacement is not from sibling stage")
                        expected = original[len(replacements)]
                        check(target.name == expected["name"] and Path(source).read_bytes() == expected["data"],
                              "Replacement order or staged bytes changed")
                        check(consumed == [doc["name"] for doc in original], "Publishing began before complete input validation")
                        if not replacements:
                            staged_bytes = [item.read_bytes() for item in root.iterdir()
                                            if item.is_file() and item.name not in before]
                            check(all(doc["data"] in staged_bytes for doc in original), "Not all documents staged before first replace")
                        replacements.append(target.name)
                    return real_replace(source, destination, *args, **kwargs)

                def callback(name, path):
                    check(isinstance(path, Path) and path == root / name, "Callback path contract changed")
                    callbacks.append((name, path.read_bytes()))

                with patch("os.replace", replace):
                    result = publish(root, once(), callback)
                check(result == [doc["name"] for doc in original], "Return order changed")
                check(replacements == result, "Each destination requires an atomic replacement")
                check(callbacks == [(doc["name"], doc["data"]) for doc in original], "Callback order/visibility changed")
                check(documents == original, "Documents mutated")
                check(set(item.name for item in root.iterdir()) == set(before) | set(result), "Temporary files leaked")
                check((root / "untouched").read_bytes() == b"leave alone", "Unrelated file changed")
                for name, (_, mode) in before.items():
                    check(stat.S_IMODE((root / name).stat().st_mode) == mode, "Existing permissions changed")

    # Every callback/replacement failure position, across all-existing, all-new,
    # and mixed destinations; wrappers must participate in the same transaction.
    for publish in entrypoints:
        for existing in (set(), {0, 1, 2}, {0, 2}):
            for boundary in ("callback", "replace"):
                for failure in range(3):
                    with TemporaryDirectory() as folder:
                        root = Path(folder)
                        documents = setup(root, 3, existing)
                        original = deepcopy(documents)
                        before = inventory(root)
                        callbacks, attempted = [], []
                        marker = RuntimeError("injected publication failure")

                        def replace(source, destination, *args, **kwargs):
                            if Path(destination).name in {doc["name"] for doc in original}:
                                attempted.append(Path(destination).name)
                                if boundary == "replace" and len(attempted) == failure + 1:
                                    raise marker
                            return real_replace(source, destination, *args, **kwargs)

                        def callback(name, path):
                            callbacks.append((name, path.read_bytes()))
                            if boundary == "callback" and len(callbacks) == failure + 1:
                                raise marker

                        with patch("os.replace", replace):
                            try:
                                publish(root, (doc for doc in documents), callback)
                            except Exception as exc:
                                check(exc is marker, "Original failure was replaced or wrapped")
                            else:
                                raise AssertionError("Publication failure swallowed")
                        prefix = failure + (boundary == "callback")
                        check(callbacks == [(doc["name"], doc["data"]) for doc in original[:prefix]],
                              "Callbacks continued, replayed, or reordered after failure")
                        check(inventory(root) == before, "Rollback failed to restore bytes/permissions")
                        check(set(item.name for item in root.iterdir()) == set(before), "Rollback leaked temporary/new files")
                        check(documents == original, "Failed publication mutated inputs")

    # Staging-create failure is injected through standard filesystem boundaries,
    # independent of whether the writer uses tempfile, Path, io.open or open.
    for publish in entrypoints:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            documents = setup(root, 3, {0, 2})
            before = inventory(root)
            stages, callbacks = set(), []
            marker = OSError("staging unavailable")
            real_os_open, real_open, real_io_open = os.open, builtins.open, io.open

            def opening(path, writing):
                if not writing or isinstance(path, int):
                    return
                target = Path(path)
                if target.parent == root and target.name not in before and target.name not in {doc["name"] for doc in documents}:
                    stages.add(str(target))
                    if len(stages) == 2:
                        raise marker

            def os_open(path, flags, *args, **kwargs):
                opening(path, flags & os.O_CREAT)
                return real_os_open(path, flags, *args, **kwargs)

            def standard_open(real, path, mode="r", *args, **kwargs):
                opening(path, any(flag in mode for flag in "wax+"))
                return real(path, mode, *args, **kwargs)

            with patch("os.open", os_open), patch("builtins.open", lambda *a, **k: standard_open(real_open, *a, **k)), \
                    patch("io.open", lambda *a, **k: standard_open(real_io_open, *a, **k)):
                try:
                    publish(root, documents, lambda *args: callbacks.append(args))
                except Exception as exc:
                    check(exc is marker, "Staging failure replaced")
                else:
                    raise AssertionError("Staging failure was not exercised or swallowed")
            check(callbacks == [], "Callback ran before staging completed")
            check(inventory(root) == before, "Staging failure changed outputs")
            check(set(item.name for item in root.iterdir()) == set(before), "Staging failure leaked files")

    invalid = [None, {}, {"name": "../a", "data": b"x"}, {"name": "a/b", "data": b"x"},
               {"name": "a\\b", "data": b"x"}, {"name": ".", "data": b"x"},
               {"name": "a\x00", "data": b"x"}, {"name": 3, "data": b"x"},
               {"name": "a", "data": "x"}, {"name": "a", "data": bytearray(b"x")}]
    for publish in entrypoints:
        for invalid_document in invalid:
            with TemporaryDirectory() as folder:
                root = Path(folder)
                documents = setup(root, 1, {0}) + [invalid_document]
                before = inventory(root)
                callbacks = []
                expected = outcome(lambda: baseline.publish_files(root, deepcopy(documents)))
                check(outcome(lambda: publish(root, deepcopy(documents), lambda *a: callbacks.append(a))) == expected,
                      "Validation error changed")
                check(inventory(root) == before and callbacks == [], "Invalid bundle caused side effects")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            documents = setup(root, 1, {0})
            before = inventory(root)
            for suffix in ([{"name": "file-0", "data": b"again"}], [{"name": "file-0", "data": 1}]):
                batch = documents + suffix
                check(outcome(lambda: publish(root, batch, None)) == outcome(lambda: baseline.publish_files(root, batch)),
                      "Duplicate/data validation priority changed")
                check(inventory(root) == before, "Duplicate bundle changed files")
            (root / "directory").mkdir()
            try:
                (root / "link").symlink_to(root / "untouched")
            except (OSError, NotImplementedError):
                # Some Windows accounts cannot create symlinks. Exercise the same
                # non-regular-target contract with a second directory, preserving
                # two real checks and the protocol count on supported platforms.
                (root / "link").mkdir()
            for name in ("directory", "link"):
                batch = documents + [{"name": name, "data": b"x"}]
                check(outcome(lambda: publish(root, batch, None)) == outcome(lambda: baseline.publish_files(root, batch)),
                      "Non-regular target accepted or error changed")
                check((root / "file-0").read_bytes() == b"old-0", "Target validation happened after a write")
            missing = root / "absent"
            check(outcome(lambda: publish(missing, [], None)) == ("raise", "ValueError", ("Output directory does not exist",)),
                  "Missing directory behavior changed")
            check(outcome(lambda: publish(missing, [None], None)) == ("raise", "ValueError", ("Document must be an object",)),
                  "Document/directory validation order changed")

    with TemporaryDirectory() as folder:
        root = Path(folder)
        result = caller.publish_release(root, "v\u03b2", "Caf\u00e9\nReady")
        check(result == {"version": "v\u03b2", "written": ["version.txt", "notes.txt"]}, "Release caller changed")
        check((root / "notes.txt").read_bytes() == "Caf\u00e9\nReady".encode(), "Text encoding changed")
        before = inventory(root)
        check(outcome(lambda: candidate.publish_text(root, [("../bad", "x"), ("later", 3)]))
              == outcome(lambda: baseline.publish_text(root, [("../bad", "x"), ("later", 3)])),
              "Text conversion must precede filename validation")
        check(inventory(root) == before, "Text conversion failure wrote files")
        marker = LookupError("release callback failed")
        callbacks = []

        def callback(name, path):
            callbacks.append(name)
            if name == "notes.txt":
                raise marker

        try:
            caller.publish_release(root, "new", "new notes", callback)
        except Exception as exc:
            check(exc is marker, "Text wrapper failure changed")
        else:
            raise AssertionError("Text wrapper swallowed callback error")
        check(callbacks == ["version.txt", "notes.txt"], "Text callback order changed")
        check(inventory(root) == before, "Text wrapper did not roll back")
    return count


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]))}))
