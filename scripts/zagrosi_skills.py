#!/usr/bin/env python3
"""Import a private package exclusively from a complete, SHA-256-bound manifest."""
from __future__ import annotations

import builtins
import hashlib
import importlib.abc
import importlib.util
import os
import json
import keyword
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from types import MappingProxyType


_SOURCE_LIMIT = 16 * 1024 * 1024


def _regular_bytes(descriptor: int, path: Path) -> bytes:
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _SOURCE_LIMIT:
            raise ImportError(f"Not a bounded single-link regular source: {path}")
        data = source.read(_SOURCE_LIMIT + 1)
        after = os.fstat(source.fileno())
        def signature(item):
            return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if len(data) > _SOURCE_LIMIT or signature(before) != signature(after):
            raise ImportError(f"Source changed while reading: {path}")
        return data


def _read_source(path: Path) -> bytes:
    strict = all(hasattr(os, flag) for flag in ("O_CLOEXEC", "O_NOFOLLOW", "O_DIRECTORY")) and os.open in os.supports_dir_fd
    if not strict:
        chain = (path, *path.parents)
        before = [item.lstat() for item in chain]
        if any(stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 for info in before):
            raise ImportError(f"Linked source path: {path}")
        data = _regular_bytes(os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)), path)
        after = [item.lstat() for item in chain]
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_file_attributes", 0))
        if list(map(identity, before)) != list(map(identity, after)):
            raise ImportError(f"Source path changed while reading: {path}")
        return data
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        return _regular_bytes(os.open(path.name, flags | os.O_NONBLOCK, dir_fd=directory), path)
    finally:
        os.close(directory)


def bootstrap(manifest: dict[str, str], cli_path: Path, namespace: str):
    """Validate all sources, then lazily execute only their verified byte snapshots."""
    forbidden = set(sys.stdlib_module_names) | set(sys.builtin_module_names) | set(dir(builtins))
    identifier = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
    if not isinstance(namespace, str) or not identifier.fullmatch(namespace) or namespace in forbidden or keyword.iskeyword(namespace):
        raise ImportError("Invalid or shadowing package namespace")
    if any(name == namespace or name.startswith(namespace + ".") for name in sys.modules):
        raise ImportError(f"Package namespace already exists: {namespace}")
    if not isinstance(manifest, dict) or not manifest:
        raise ImportError("An explicit nonempty source manifest is required")
    cli_path = Path(os.path.abspath(cli_path))
    try:
        source_root = cli_path.resolve(strict=True).parent
    except (OSError, RuntimeError) as exc:
        raise ImportError("Cannot resolve the runtime source directory") from exc
    modules, hashes, root = {}, {}, None
    for relative, digest in dict(manifest).items():
        if not isinstance(relative, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ImportError("Invalid source manifest entry")
        path = PurePosixPath(relative)
        if path.is_absolute() or str(path) != relative or ".." in path.parts or len(path.parts) < 2 or path.suffix != ".py":
            raise ImportError(f"Invalid source path: {relative}")
        parts = (*path.parts[:-1], path.stem)
        package = parts[-1] == "__init__"
        names = parts[:-1] if package else parts
        if any(not identifier.fullmatch(name) or name in forbidden or keyword.iskeyword(name) or name.startswith("__") for name in names):
            raise ImportError(f"Invalid or shadowing module path: {relative}")
        if root is None:
            root = names[0]
        if names[0] != root:
            raise ImportError("All modules must belong to one package directory")
        name = ".".join((namespace, *names[1:]))
        if name in modules or name in {namespace + ".CLI_PATH", namespace + ".MODULE_NAMES", namespace + ".verify_sources"}:
            raise ImportError(f"Module name collision: {name}")
        modules[name] = (source_root / relative, package)
        hashes[name] = digest
    if namespace not in modules or not modules[namespace][1]:
        raise ImportError("Package __init__.py is missing")
    for name in modules:
        if name != namespace and (name.rpartition(".")[0] not in modules or not modules[name.rpartition(".")[0]][1]):
            raise ImportError(f"Parent package is missing: {name}")

    def verify_sources():
        checked = {}
        for name, (path, _) in modules.items():
            try:
                data = _read_source(path)
            except OSError as exc:
                raise ImportError(f"Cannot safely read source: {path}") from exc
            if hashlib.sha256(data).hexdigest() != hashes[name]:
                raise ImportError(f"Source digest mismatch: {path}")
            checked[name] = data
        return checked

    sources = verify_sources()  # Nothing is compiled or executed before this completes.

    class BoundModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname != namespace and not fullname.startswith(namespace + "."):
                return None
            if fullname not in modules:
                raise ModuleNotFoundError(f"Unbound module: {fullname}", name=fullname)
            filename, package = modules[fullname]
            return importlib.util.spec_from_loader(fullname, self, origin=str(filename), is_package=package)

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            path, package = modules[module.__name__]
            module.__file__, module.__cached__ = str(path), None
            if package:
                module.__path__ = []
            if module.__name__ == namespace:
                module.CLI_PATH = cli_path
                module.MODULE_NAMES = MappingProxyType({str(path.relative_to(source_root)): name for name, (path, _) in modules.items()})
                module.verify_sources = verify_sources
            exec(compile(sources[module.__name__], str(path), "exec", dont_inherit=True), module.__dict__)

    finder = BoundModules()
    package = importlib.util.module_from_spec(finder.find_spec(namespace))
    if sys.modules.setdefault(namespace, package) is not package:
        raise ImportError(f"Package namespace already exists: {namespace}")
    sys.meta_path.insert(0, finder)
    try:
        finder.exec_module(package)
    except BaseException:
        sys.meta_path.remove(finder)
        for name in list(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]
        raise
    return package


# BEGIN RUNTIME MANIFEST
RUNTIME_MANIFEST = {
    "forge/__init__.py": "f6b2a10e6ee7118cac49c823f53216f5952058d206db18bf681db53864afa73f",
    "forge/artifacts.py": "75481b5cea574f58aa5ed614de5341a9b4a69f95d62ecb2a8439723dc2a99780",
    "forge/authority.py": "dc3b592eeb2deba8e5778887f68c1435c4b04a7b215bc6b7f028c8a995563b41",
    "forge/capabilities.py": "e343bea5ebe632737ef32c5c84f94802ee428e9447ecc1a094ee88cbb42d297e",
    "forge/cli.py": "65c4246affa8db5f12c1668a58e572116b2091164c5e71617004bfcca5a0b319",
    "forge/context.py": "d2bf931f26f9194e0a813ff6893b1b7ecbac324aefeafa6ff7523b793165ce75",
    "forge/detached_authority.py": "059cbcc328d299aed829d1e0e8c04685250bd0d213455cf3b265980e7971d0c8",
    "forge/detached_context.py": "b58b36c1f298409caec92ac7b0262a00fbc5e8d5bc838fec3b5a872cfb7e050c",
    "forge/detached_contract.py": "3be67a543a7309433123df5b4bf673821b1d1a9bdfcdbc06c05d1cfbf9b1ab6b",
    "forge/detached_progress.py": "383f22f5524be50aecc8d372ff681c85f119eec0fce78e3974f1c64583825de1",
    "forge/detached_record.py": "e16879b1c542e04b871b21bd46adf23486c3e41897fc46b99b4883a9c57ebb21",
    "forge/detached_setup.py": "37b0bb36509f909cff8af15bc14b517dbe9917bb4f764fe7938966b8dfdf1816",
    "forge/detached_state.py": "6a14bff32d2c3d2a93adef7c23a9230b36002db71f8234e04289eeae84fe661e",
    "forge/diffs.py": "8ee0bd77fb807eeb418c40f30e8c61be10284546433d6c7504376b1f6d239c68",
    "forge/doctor.py": "922b5b0f4da399fdfd1d7e3bdef04d083342b708ed526fa3c80c1c0fcf03567f",
    "forge/evaluations.py": "ef9b1bca7d4dc8b0f5d85d0f4e9eb45b5741a161224c485dc353577b88bce2e9",
    "forge/evidence.py": "e5d264336357937bbe55a7331cd71c88b02dd65730d8bdaaf8c6cd68b4180480",
    "forge/flights.py": "dbbbc2e736cc91fc8aa3fa353f505fb7fa9fc9f543a9fadbc88355241c21d825",
    "forge/gates.py": "cf20f84f2cfaf7f6ff5c9f73b1815dbcc544c806edfbd0e7ba017a59f5f8da10",
    "forge/handoff.py": "bd1f9f47c2b2b83dc81bc30fa380b70853f34010bc60819e248c77230dd3327e",
    "forge/handoff_host.py": "65fde29e25b77efc17d3d2df0e3f417bd193b64d052fd00b70ce6244ea8df010",
    "forge/handoff_wire.py": "43477d8cc74aa8c015e6f5780ee3daade133182291ce84a99acf0725952bbed0",
    "forge/installation.py": "bc2f23259cc5f3e083f51e0fa2389de173af11285c55e409c55adc47da7efec2",
    "forge/locks.py": "9810b84e13ee0c6adda270756d73aa5a2abd7c6c43746dc3babe03c12130999a",
    "forge/markdown.py": "e3f422f243d5377daa92bfe28c2a5ba691d158a84e608b18cdb6634013050e4d",
    "forge/models.py": "32e241a6442e14753e91cf5c68949842a7d9d53d070f2f34efd87647fa5f18d3",
    "forge/output.py": "cae3ddf50b54c235621d37d77fa309dfd564fe98136dd4117e0fbe9f858318e3",
    "forge/ownership.py": "dac57fa5ea286107ad5f1c88d80fc2ad70417efc67d728b2a526c5f4bfbd80c6",
    "forge/pinners.py": "0f5ad71300d8f102ce46d9b33942f8419f023f7f22d1f6ca37f51f888476ea38",
    "forge/planning_snapshot.py": "4e38bb61e6c601cfdea492c1aa74e485792592f9b6594db58ce3fd2d3635d32e",
    "forge/planning_tools.py": "62e79301e0949458974cf3a8779fb5808ce4d5f7848f25b2331ab317e15102e8",
    "forge/policy.py": "4c7a7609124dd66f834675ec38a8ee0f39c363355b8ccf602370455ac7d19527",
    "forge/processes.py": "c9dbfda15593d10f7904749252fe3bf05beb069cd3565a9a884ee52e2bd10e66",
    "forge/projects.py": "28da68c0abb1e1f75745ad7eba44c806207566ff6062865070c6c3b15b7e8a13",
    "forge/prompts.py": "f3b6eaffd5e7fa5617381ed3a0cc27d9f25fb6cd653559a4ff0d5be3a30bc970",
    "forge/quality.py": "10105e6c19a442e1467edbb0a9873c10c62b3a9d2f01c05df364a58973b61621",
    "forge/recovery.py": "66eb8dbbf6e964c3b5f7363eb55015a3b8ee6f6206c268a94f08ad1f52ee2ece",
    "forge/scheduling.py": "af0d7773e86acea96f842fadc2e21f5b7948d92839ed4094e7bb9a8d1069e71f",
    "forge/scoring.py": "430937d3640eee5223abc0f14c56c694ccd5d2b112ac9006c8653dc16724ff3e",
    "forge/sections.py": "e505e1d093b5b53e7809063c915b48d94cd897db6e45c52f7c01a21e28288568",
    "forge/secure_io.py": "2c529854508436ff083d3a01c1ad97de4b5e5a532c1365b28a173c0cd0401108",
    "forge/session.py": "73429e3b1a2b8a5a9acf5b3816851895855904660b02602431ae4bdb1e2d9076",
    "forge/sources.py": "266daf4d71579e3d4008ad8fb26b1cba2d841f25db0029cc6b4070e9881b4c4c",
    "forge/state.py": "c7c497fe77d3308df84961ca0e178c914bec6a25765e84acf98e0b79fcfa7600",
    "forge/status.py": "4b5b60da75878188a90bbec241c22b7fd1f45cdb4cd94bd9327d390f7efe1489",
    "forge/storage.py": "f67b9a4e704d8a25e5be84239e341212301aaee40a39aeb358e26abea1c8bbc3",
    "forge/traceability.py": "59e674a65646a376843a2af3d5cbb0487ec836610db375712cae15609ade44b0",
    "forge/transaction_io.py": "1dad8fe6dab8d7d59b4bd12f9bbbd44200d7f661d8d1cc58c43b7cf93524a467",
    "forge/transaction_state.py": "1ee1194d0816d63b3c4afae1ffd21408ed6251a013a50eea934f894a87fc3c8b",
    "forge/validation.py": "8b65c2030d2b09bae794684a8cf5bd3a624ddf0430d70feacd10190b4a7dcd0a",
    "forge/workflows.py": "be789db7cfc11f3934023021587ac206ca0c240b4fcd86a166ad04c7d267252b"
}
# END RUNTIME MANIFEST

_runtime = None


def exact_handoff_cli_shape(raw_args: list[str]) -> bool:
    if len(raw_args) != 5 or raw_args[0] != "implement-evidence-handoff":
        return False
    pairs: dict[str, str] = {}
    for offset in (1, 3):
        option = raw_args[offset]
        value = raw_args[offset + 1]
        if option not in {"--implementation-root", "--section"} or option in pairs or not value:
            return False
        pairs[option] = value
    return set(pairs) == {"--implementation-root", "--section"} and pairs["--section"] in {"S26", "S28"}



def load_runtime():
    global _runtime
    if _runtime is None:
        package = bootstrap(RUNTIME_MANIFEST, Path(__file__).absolute(), __name__ + "_runtime")
        package.exact_handoff_cli_shape = exact_handoff_cli_shape
        _runtime = package
    return _runtime


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    handoff = "implement-evidence-handoff" in raw_args
    if handoff and not exact_handoff_cli_shape(raw_args):
        return 2
    try:
        package = load_runtime()
    except ImportError:
        if handoff:
            payload = {
                "schema": "zagrosi-privileged-evidence-handoff-error-v1",
                "purpose": "zagrosi_privileged_evidence_handoff_error",
                "section": raw_args[raw_args.index("--section") + 1],
                "status": "failed", "closed_error_code": "HANDOFF_AUTHORITY_INVALID",
            }
            code = 5
        else:
            payload = {"success": False, "error_code": "implement-source-drift",
                       "message": "Forge runtime sources failed integrity verification. Restore the complete plugin bundle."}
            code = 1
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return code
    cli = importlib.import_module(package.MODULE_NAMES["forge/cli.py"])
    return cli.main(raw_args)


if __name__ == "__main__":
    sys.exit(main())
