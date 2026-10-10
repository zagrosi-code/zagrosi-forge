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
    "forge/actions.py": "9e01b2dbae283e33f251bf2132ee2a4d8054d2b008f415e07a6a92e7f496530d",
    "forge/artifacts.py": "fbd866a60754a144d51c4977ef29a16510cd1b5600d14460be27123c7070d786",
    "forge/authority.py": "dc3b592eeb2deba8e5778887f68c1435c4b04a7b215bc6b7f028c8a995563b41",
    "forge/capabilities.py": "156ccf97e99dabc99c0ae5aff3671984bb9a1042d024ba24e1d18aa4cde7fcbc",
    "forge/child_process.py": "001accaf93f0d775f52073c7f1c7880887cf99a2c0cb1517095485c8d154f8c3",
    "forge/cli.py": "d66ce469e0fcd71d49a065a133185dc1a2d343604a15a7cafdb718bae2b5d7e2",
    "forge/codex_config.py": "e8aa857d1243b9e494eee02bb09240b8623d43cd34007e84220e905f77156f5c",
    "forge/command_args.py": "46ec41dc6480f0a0ba5220cc4d73044c5f6ddd70d80b5b7af1c0016c9c7fa8d7",
    "forge/compatibility.py": "4d2f1a5402db12f836e8965a4ea8a910838e2363a05732d01d0673a17dff39e0",
    "forge/context.py": "048706f79fad77eeaf800da31eabb8e70b4d72f049d327351a05115f1b0ca596",
    "forge/context_links.py": "f5cb5ba95ccf44d853ec54134f45be6a44803b65089c54850d68d14949806b13",
    "forge/detached_authority.py": "059cbcc328d299aed829d1e0e8c04685250bd0d213455cf3b265980e7971d0c8",
    "forge/detached_context.py": "0ecc75152bd3a1ab8004869eb0e442593ead9c0f6b9a10ea1656dca0482f8fc9",
    "forge/detached_contract.py": "74a02cd1628cca055e02b5240900b3dbe10b17aa0401940d1c16584bb0b044d7",
    "forge/detached_progress.py": "44f3a91eeca69b209c69a9a5c56677e27f89dba9586d3a7d05a3c83ab8b8e8a4",
    "forge/detached_record.py": "8a4d5725e8312a1e025152e1d35c20c08e1be09c97c326bd31752a9dce935632",
    "forge/detached_setup.py": "9e7f6e329ccd46ba17d8ef39b235668e41bc73ec3205461737a582e4044081f7",
    "forge/detached_state.py": "6a14bff32d2c3d2a93adef7c23a9230b36002db71f8234e04289eeae84fe661e",
    "forge/diffs.py": "8ee0bd77fb807eeb418c40f30e8c61be10284546433d6c7504376b1f6d239c68",
    "forge/doctor.py": "3a0541074ad16cd47fe6cc40d45dd50f95d0e579e23ba5d33840007c166c509a",
    "forge/evaluations.py": "ef9b1bca7d4dc8b0f5d85d0f4e9eb45b5741a161224c485dc353577b88bce2e9",
    "forge/evidence.py": "b162cdd6f77960163dd7b2fc43fa572ccbd723f9777097c60cfdcd7d689c13ae",
    "forge/flights.py": "cfaa6a48301f6e9f333a32ec3da081cb722ec3cd86e619e0c3763fa6f7079ae9",
    "forge/gates.py": "5fbd9a87344e780bde5b8c990038c0ccec3e8dbb39286e94525878a9b294f3aa",
    "forge/handoff.py": "f4f7e4ce6d71305c0e2e15023152687ed598454a6638c634bb5637810bd9f7bc",
    "forge/handoff_host.py": "e47298e46bdaeb336e6537436cf3c77ce63ce98ab797e3ff1a26749d1a7d023f",
    "forge/handoff_wire.py": "43477d8cc74aa8c015e6f5780ee3daade133182291ce84a99acf0725952bbed0",
    "forge/installation.py": "4c488e36b1e50ed3b3a5a9a51a7b3b16571d1a061b8704f5670913f03ede3b5a",
    "forge/locks.py": "9810b84e13ee0c6adda270756d73aa5a2abd7c6c43746dc3babe03c12130999a",
    "forge/markdown.py": "e890dae35120d93cef771bffc991d44ea59da9ba43af458b35daf60b320e0d97",
    "forge/models.py": "939f0ff5b2936143e7265657dd51f3fc757d02a85cf68b796e8b939100efafec",
    "forge/mutable_inputs.py": "071dcb5530a3fa42ba64b2653519abfdbf7dce00f4af065b26cfd7c2a62cc627",
    "forge/output.py": "0f0fe82bbc1714033f2746bf6e22f87677838da31644af588efe96cfacd24609",
    "forge/ownership.py": "7dfda3deeb6f1c0e19ce9d1636507f628808ad4fbd9385e58dda8dc95e8fcca4",
    "forge/pinners.py": "6f5948c2d379fea95f67b6d1b5a6833c257e0c71101145ddb3b12a820afb4da3",
    "forge/planning_contract.py": "3889f2998bd95dd382ebb280473472ec4e576f4a9a1cfe8f023c1b41e959a737",
    "forge/planning_snapshot.py": "4e38bb61e6c601cfdea492c1aa74e485792592f9b6594db58ce3fd2d3635d32e",
    "forge/planning_tools.py": "62e79301e0949458974cf3a8779fb5808ce4d5f7848f25b2331ab317e15102e8",
    "forge/plugin_cache.py": "78a040cf3839b0c9db92525d11fcfd208e6a548b5160a8572210fab0ff33d399",
    "forge/policy.py": "2a8f539bac65ff1b8a98f9c93867b32b435acb2c4bac375727f82ce5826d902d",
    "forge/processes.py": "c9dbfda15593d10f7904749252fe3bf05beb069cd3565a9a884ee52e2bd10e66",
    "forge/projects.py": "496988d203dc08dfcd96e0be894a4b7bee5b87848106ca84fa2b86c483c75dac",
    "forge/prompts.py": "98165c170b655fb72078c09d02e294f48f944d815385449d83abfd9fbae6e1eb",
    "forge/provider_output.py": "95fa0dbbb231cf37f190ad1e98df941c3324e618154027d7931110834251a250",
    "forge/provider_preflight.py": "9a54f3cfe83735517b6e85d0d9d1fbe2e566fd141ecdd8102195617a0d5e8da4",
    "forge/providers.py": "b3babfba96ceae5590b11a22f15693fa22f71591679dd83f08de17f20445febd",
    "forge/quality.py": "07b65d5bfe65cce538a96a17cacf92309d2788e50bc2fe149d7abec8385cd1e6",
    "forge/recovery.py": "e3ccfd05e6f5d8177be70dcfa0a111c0665c2da876979c3c0437b56edbb1a667",
    "forge/resume.py": "083638cfd6f7183063f13ef0923484b18de75018310a4bb8908df4e00f73bf5d",
    "forge/scheduling.py": "b3092154426f9f2c466ab84800ee21c816dc9d42ce39b151f7ca070ef22555f7",
    "forge/scoring.py": "fa17960b16f1215958653c86efa7e4fe246bd5df8077d6b84d61e538e7362750",
    "forge/sections.py": "f3875f1d448afc383bae5077b010d4914534238e2d354c5ff013ab083d07f32d",
    "forge/secure_io.py": "2c529854508436ff083d3a01c1ad97de4b5e5a532c1365b28a173c0cd0401108",
    "forge/session.py": "87d35fb6a68e016f6480d7fa083842022f535e4c5385529202498f71b4bbbc02",
    "forge/sources.py": "72663d289ed6bd1cf002fdcb015b0d4d13098d645557a8945d2807091184f476",
    "forge/state.py": "7114e8bb671c3adbf8de5fa6d247455ce53a3f23c75eb35612898ed443289f50",
    "forge/status.py": "da739435e3d930bdd14cd31ad42da36e500a12a772bd39505169efcca77a601c",
    "forge/storage.py": "e7c726dbfa07ec7ac4377bc780382fdcbca836935e04aad0caccfdcf0ffac2dd",
    "forge/team.py": "f967686f91068877ead7d93b1a6c532dcbfbf18d2f3e5cda0162b1ff9284f971",
    "forge/team_cli.py": "31f1ea1d9be561dbf858c6426a01c74cb5c360d5d544c33d6d8136b6fb58f945",
    "forge/team_config.py": "7fd0801cdeec22917dc58d261f9c6745a123038105f113c3bada5f760d69f93a",
    "forge/team_dependencies.py": "60219c09be982e843f2e65c1bd9de0036d7a9938f9ad2742e273dbcd5f936b72",
    "forge/team_git.py": "d7508355403f251f53bc40421a217e7fe8417ec11100d6f9404a735dd47b71ed",
    "forge/team_plans.py": "77e87f7afb0ae24477e88ccb455ee4f78761d3491a10f988fc9c4f6ab631b101",
    "forge/team_state.py": "dd198aded0736c0bd80b4d15820cd87f26c76e2443334527fbece7caf6080bee",
    "forge/team_workflow.py": "ade4afcf6ecb004af960395ca629daa32e7b512164224bd63e7b02dc44a0832a",
    "forge/traceability.py": "812629f4c2b8ca4a49a69efab674b3495ccbbcd8510f5b4a532519a550561b4f",
    "forge/transaction_io.py": "1dad8fe6dab8d7d59b4bd12f9bbbd44200d7f661d8d1cc58c43b7cf93524a467",
    "forge/transaction_state.py": "1ee1194d0816d63b3c4afae1ffd21408ed6251a013a50eea934f894a87fc3c8b",
    "forge/unit12_adapter.py": "6319a6178572e603a7ec4767a9650e045af1b9afc3051c697dfb2b0317df553d",
    "forge/validation.py": "af2205b0318250700e159987b8be4c20eb50054650d126b7798c5cdeb6c5065a",
    "forge/verification.py": "81b44e81ba3262d082666f2b7565d2d3c011b3169a91497efd6cc64c7be7ba32",
    "forge/workflows.py": "08a3732d3b7482c01bf049cd0eb5185438b0c3cda3794dcbc006fd5d8204671c"
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
