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
                module.MODULE_NAMES = MappingProxyType({path.relative_to(source_root).as_posix(): name for name, (path, _) in modules.items()})
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
    "forge/artifacts.py": "960d8704ba68d64cd629432a92d5e9fbee0f1dd2b576b76c174e7a364c1c3084",
    "forge/authority.py": "dc3b592eeb2deba8e5778887f68c1435c4b04a7b215bc6b7f028c8a995563b41",
    "forge/capabilities.py": "e343bea5ebe632737ef32c5c84f94802ee428e9447ecc1a094ee88cbb42d297e",
    "forge/cli.py": "90f6f1c45e15937262a5ef290b63d9c50e4a1df4a5381bf3c290f69ad84ceb6a",
    "forge/codex_config.py": "27c39503fb15aed4b8d9a3e63b140d099a0811d46ad1de2690a49f2ebea65c57",
    "forge/context.py": "3a5ea24572b12516d22a0a0ac4b3b6af2dc82b31a79cf766d43b29630457cb1b",
    "forge/context_links.py": "23da67cc662631aec6780c1c0600ecbdb84cb6f0270a3f7a3b4bafef2fab72c2",
    "forge/detached_authority.py": "059cbcc328d299aed829d1e0e8c04685250bd0d213455cf3b265980e7971d0c8",
    "forge/detached_context.py": "0ecc75152bd3a1ab8004869eb0e442593ead9c0f6b9a10ea1656dca0482f8fc9",
    "forge/detached_contract.py": "57ed0b98496842b39933bdba4e3308fefc2eb1d79c2e32eb44742f7525bc06bc",
    "forge/detached_progress.py": "44f3a91eeca69b209c69a9a5c56677e27f89dba9586d3a7d05a3c83ab8b8e8a4",
    "forge/detached_record.py": "8a4d5725e8312a1e025152e1d35c20c08e1be09c97c326bd31752a9dce935632",
    "forge/detached_setup.py": "9e7f6e329ccd46ba17d8ef39b235668e41bc73ec3205461737a582e4044081f7",
    "forge/detached_state.py": "6a14bff32d2c3d2a93adef7c23a9230b36002db71f8234e04289eeae84fe661e",
    "forge/diffs.py": "8ee0bd77fb807eeb418c40f30e8c61be10284546433d6c7504376b1f6d239c68",
    "forge/doctor.py": "922b5b0f4da399fdfd1d7e3bdef04d083342b708ed526fa3c80c1c0fcf03567f",
    "forge/evaluations.py": "ef9b1bca7d4dc8b0f5d85d0f4e9eb45b5741a161224c485dc353577b88bce2e9",
    "forge/evidence.py": "8b5361e7eef375516c71390746f62443db8e7c3229eea25202e30d63fbbd39ca",
    "forge/flights.py": "afb5710355305837673f3c59479d0933e19f48e2111506ced69abd59a1924e8c",
    "forge/gates.py": "78757927601394ab417ca0f3ff665b57522674be36a896a2347885d17e032352",
    "forge/handoff.py": "f4f7e4ce6d71305c0e2e15023152687ed598454a6638c634bb5637810bd9f7bc",
    "forge/handoff_host.py": "e47298e46bdaeb336e6537436cf3c77ce63ce98ab797e3ff1a26749d1a7d023f",
    "forge/handoff_wire.py": "43477d8cc74aa8c015e6f5780ee3daade133182291ce84a99acf0725952bbed0",
    "forge/installation.py": "614a9edaf476ce24abcb1526d130565129a193069b0a3751a5699e77624cb91f",
    "forge/locks.py": "9810b84e13ee0c6adda270756d73aa5a2abd7c6c43746dc3babe03c12130999a",
    "forge/markdown.py": "8c6df4b14156116d9cec38ec97eb5922754851bdb3dd243c076a54c3ca4234f6",
    "forge/models.py": "32e241a6442e14753e91cf5c68949842a7d9d53d070f2f34efd87647fa5f18d3",
    "forge/mutable_inputs.py": "50ee8a70dfc85d32188fc89947e67422d24c67eceaa053f49f7a09241d7b8da9",
    "forge/output.py": "cae3ddf50b54c235621d37d77fa309dfd564fe98136dd4117e0fbe9f858318e3",
    "forge/ownership.py": "7dfda3deeb6f1c0e19ce9d1636507f628808ad4fbd9385e58dda8dc95e8fcca4",
    "forge/pinners.py": "6f5948c2d379fea95f67b6d1b5a6833c257e0c71101145ddb3b12a820afb4da3",
    "forge/planning_snapshot.py": "4e38bb61e6c601cfdea492c1aa74e485792592f9b6594db58ce3fd2d3635d32e",
    "forge/planning_tools.py": "62e79301e0949458974cf3a8779fb5808ce4d5f7848f25b2331ab317e15102e8",
    "forge/plugin_cache.py": "f6a3edd0062016731cda9b5de94317bb7e786563408f2a70f24713eff1586e00",
    "forge/policy.py": "d5d176ea574f4a2c3fe1a298203521d2a0ab1587e1bf801e3648b7ef5f2524e6",
    "forge/processes.py": "c9dbfda15593d10f7904749252fe3bf05beb069cd3565a9a884ee52e2bd10e66",
    "forge/projects.py": "14b0065adb8b990a8c402aa5fe138d8e4f01b694ee44169fa4cf00a6b42a4814",
    "forge/prompts.py": "98165c170b655fb72078c09d02e294f48f944d815385449d83abfd9fbae6e1eb",
    "forge/quality.py": "f330118be1f3c6e7f812306ed6ae023cf51ff24cde0552e8f3c3abe0dd04428a",
    "forge/recovery.py": "e3ccfd05e6f5d8177be70dcfa0a111c0665c2da876979c3c0437b56edbb1a667",
    "forge/resume.py": "91c0091b630fd9533c0bbb6de9c50d39b2f545111e04b704d0a5a0460bb2fe1d",
    "forge/scheduling.py": "c15ad3757b3ebe431d1c40726d80ecd6ae30b4851ca87bef07508cf7a5fdd4f9",
    "forge/scoring.py": "fa17960b16f1215958653c86efa7e4fe246bd5df8077d6b84d61e538e7362750",
    "forge/sections.py": "f3875f1d448afc383bae5077b010d4914534238e2d354c5ff013ab083d07f32d",
    "forge/secure_io.py": "2c529854508436ff083d3a01c1ad97de4b5e5a532c1365b28a173c0cd0401108",
    "forge/session.py": "389b3b22ec27e1411340858aeda58dc67a605bda2853e85a7cbd7a683e93c8fa",
    "forge/sources.py": "8fa0ee596dfec87c29ece2d99c513b0f8019cab38f81a449348d6515f2985c27",
    "forge/state.py": "f6f0260c9479a94a3b7c06091f931a4ca1b1c33a6bea53b6da4db02f2b5d0c7b",
    "forge/status.py": "58ac9783533ca62d732235fe5107af715671319b18d5bec16eb010b0f3f3313f",
    "forge/storage.py": "f8a39c5e2d827b176a51713094845b070f300ba0864526b57ffefeafc989ff4e",
    "forge/traceability.py": "04219723b40efa1d9956b2e956cf20ae1d5358d2870c5dea873adb0cfebeb395",
    "forge/transaction_io.py": "1dad8fe6dab8d7d59b4bd12f9bbbd44200d7f661d8d1cc58c43b7cf93524a467",
    "forge/transaction_state.py": "1ee1194d0816d63b3c4afae1ffd21408ed6251a013a50eea934f894a87fc3c8b",
    "forge/unit12_adapter.py": "6319a6178572e603a7ec4767a9650e045af1b9afc3051c697dfb2b0317df553d",
    "forge/validation.py": "68d84742ac201af2a1052979cdab20b379bdb47a485ed86a749b35e75302cb72",
    "forge/workflows.py": "b51975b25a88461ab5eeb2082c62d4fcf83fd6806b7c8b3a375f0d498051100c"
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
