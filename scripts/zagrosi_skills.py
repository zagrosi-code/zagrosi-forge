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
import signal
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
    "forge/artifacts.py": "2a2cf185b4282878c8c6a418658d0fb1d7057a3bc0912c5de36ace200e556f00",
    "forge/authority.py": "dc3b592eeb2deba8e5778887f68c1435c4b04a7b215bc6b7f028c8a995563b41",
    "forge/capabilities.py": "ebef1571dc7a3c2a2ea5a2559270e4fda63effc0aedfcd69e732332df1d1b10a",
    "forge/child_process.py": "257b32bf2dca70fd00f4ea76f43633163633aa5c2d258f784560e1cdadbe023e",
    "forge/cli.py": "eb5bf81126224f45754a2dcd9ee0069e220c75d12590900370de425ee8f161dc",
    "forge/codex_config.py": "9df770ee4f828c1664aa9a66beb2ec8ea73da8923ba84257350acfd031414ae9",
    "forge/command_args.py": "46ec41dc6480f0a0ba5220cc4d73044c5f6ddd70d80b5b7af1c0016c9c7fa8d7",
    "forge/compatibility.py": "c7699d0801900cadf27dc5dea0536e09e86daf2b72fa4e6a1889b03c072fd8cc",
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
    "forge/doctor.py": "f5b407386d8fb53209121cf57a3a8515583daac60a0adea5325f286c5c1c6a87",
    "forge/evaluations.py": "302b70123f249a29d5e15b082b55335be1cc7e1ee5c4ce69514cc41bb2fe93ff",
    "forge/evidence.py": "b162cdd6f77960163dd7b2fc43fa572ccbd723f9777097c60cfdcd7d689c13ae",
    "forge/flights.py": "8727e27ea649a37a55bb783e58ba9e96b717816f8103601cf1ebc01b978202b2",
    "forge/gates.py": "5fbd9a87344e780bde5b8c990038c0ccec3e8dbb39286e94525878a9b294f3aa",
    "forge/handoff.py": "f4f7e4ce6d71305c0e2e15023152687ed598454a6638c634bb5637810bd9f7bc",
    "forge/handoff_host.py": "e47298e46bdaeb336e6537436cf3c77ce63ce98ab797e3ff1a26749d1a7d023f",
    "forge/handoff_wire.py": "43477d8cc74aa8c015e6f5780ee3daade133182291ce84a99acf0725952bbed0",
    "forge/hosts.py": "27eb081e5b394cfaa51266d614368439ed2ed869811a37c997e7477f380f1e07",
    "forge/installation.py": "64e311c29efcbc87a062ec571bdda356b954ceb4be10a6d99a0ae07732298008",
    "forge/locks.py": "9810b84e13ee0c6adda270756d73aa5a2abd7c6c43746dc3babe03c12130999a",
    "forge/markdown.py": "0e7c24e75cced3f6d7eded892b9da1338e25a15815cd8b774b96668aa3d74d41",
    "forge/models.py": "939f0ff5b2936143e7265657dd51f3fc757d02a85cf68b796e8b939100efafec",
    "forge/mutable_inputs.py": "3e93bc3ef94fa22553ea353296dcc4abe2230cb6b9f89c56f2f085fcf665cd71",
    "forge/output.py": "d39ccc56a2fe8ef75122a0d0ea36709678d37eeb0119592fc430348d6cd547ef",
    "forge/ownership.py": "7dfda3deeb6f1c0e19ce9d1636507f628808ad4fbd9385e58dda8dc95e8fcca4",
    "forge/pinners.py": "6f5948c2d379fea95f67b6d1b5a6833c257e0c71101145ddb3b12a820afb4da3",
    "forge/planning_contract.py": "f03b34588061f6c315604b693ca4fc686eeb15e8df38bebac683f4588715b342",
    "forge/planning_snapshot.py": "4e38bb61e6c601cfdea492c1aa74e485792592f9b6594db58ce3fd2d3635d32e",
    "forge/planning_tools.py": "da9e40ad58022eb14561fcac28306ee4253dba299298c1f045d6e559c1eff5a5",
    "forge/plugin_cache.py": "78a040cf3839b0c9db92525d11fcfd208e6a548b5160a8572210fab0ff33d399",
    "forge/policy.py": "456cde322c43102034c34cc5004efe016728a86bcd07be5c6a9f20cd93b55bbf",
    "forge/processes.py": "c9dbfda15593d10f7904749252fe3bf05beb069cd3565a9a884ee52e2bd10e66",
    "forge/projects.py": "496988d203dc08dfcd96e0be894a4b7bee5b87848106ca84fa2b86c483c75dac",
    "forge/prompts.py": "98165c170b655fb72078c09d02e294f48f944d815385449d83abfd9fbae6e1eb",
    "forge/provider_output.py": "300d9c3d89ad694b7ad27cc1b2c46eaa0955a4483247d59b9927fc4172845f51",
    "forge/provider_preflight.py": "9a54f3cfe83735517b6e85d0d9d1fbe2e566fd141ecdd8102195617a0d5e8da4",
    "forge/providers.py": "cfa1b3ddbe2e4d5da5e0c3ad377c07d561d24a88419b6871887da35a20c5187e",
    "forge/quality.py": "85068579c387aad2273d05d6bd6cb8ad62b29307bce755b96879c1e314b4ad71",
    "forge/recovery.py": "e3ccfd05e6f5d8177be70dcfa0a111c0665c2da876979c3c0437b56edbb1a667",
    "forge/resume.py": "083638cfd6f7183063f13ef0923484b18de75018310a4bb8908df4e00f73bf5d",
    "forge/scheduling.py": "e241990ca4ea71efb8ff8385c8ab367e336694ddcb46cd12da668ee2c43a38f3",
    "forge/scoring.py": "606e7701a437e73c456f1a182080da17b487caf41561deeade3aceaa1855b273",
    "forge/sections.py": "f236e0929ae1da8ebbca485e4298dc25595b51d97ebdf261ca69611fa6d72959",
    "forge/secure_io.py": "2c529854508436ff083d3a01c1ad97de4b5e5a532c1365b28a173c0cd0401108",
    "forge/session.py": "87d35fb6a68e016f6480d7fa083842022f535e4c5385529202498f71b4bbbc02",
    "forge/sources.py": "8ce1789d5feed177ac55233c987c6b888509906dafc219e8dbec331c34fafa71",
    "forge/state.py": "7114e8bb671c3adbf8de5fa6d247455ce53a3f23c75eb35612898ed443289f50",
    "forge/state_first.py": "b07c75730f0534c611b468860e6770945a912aa5ea2f27676767be3a489dbcc5",
    "forge/status.py": "46ecc3d79ce67e5143dfdaf15745d719e9af4b4ae8a55c8327ea475a81e4f071",
    "forge/storage.py": "fc7100b6437c556a324793079a4cee264d2b92344b2226d222cfa4a3fc4d8c4c",
    "forge/team.py": "4dac88d99d6bddc88f79ab90dd6c601e83d53d50b5d7b76d12906f89f63d4177",
    "forge/team_cli.py": "31f1ea1d9be561dbf858c6426a01c74cb5c360d5d544c33d6d8136b6fb58f945",
    "forge/team_config.py": "7fd0801cdeec22917dc58d261f9c6745a123038105f113c3bada5f760d69f93a",
    "forge/team_dependencies.py": "60219c09be982e843f2e65c1bd9de0036d7a9938f9ad2742e273dbcd5f936b72",
    "forge/team_git.py": "3349efe52aaba34186ed50bb3152da557dd7c0d3058b7fa826f65df102bd70b3",
    "forge/team_plans.py": "cff8dbb144fafb17ccd7e79ff94411e2e6ae901dfeef28b4a2eb1041625c879f",
    "forge/team_state.py": "dd198aded0736c0bd80b4d15820cd87f26c76e2443334527fbece7caf6080bee",
    "forge/team_workflow.py": "44f93edb0c0cc4650ec39e698819db94bfa3cac99cfc9464191dc0f95147444a",
    "forge/traceability.py": "812629f4c2b8ca4a49a69efab674b3495ccbbcd8510f5b4a532519a550561b4f",
    "forge/transaction_io.py": "1dad8fe6dab8d7d59b4bd12f9bbbd44200d7f661d8d1cc58c43b7cf93524a467",
    "forge/transaction_state.py": "1ee1194d0816d63b3c4afae1ffd21408ed6251a013a50eea934f894a87fc3c8b",
    "forge/unit12_adapter.py": "6319a6178572e603a7ec4767a9650e045af1b9afc3051c697dfb2b0317df553d",
    "forge/validation.py": "cf7e01e88c9b563dbc2f4f753c06e10c0f1b865f6b64ec2849db9f3d201c8224",
    "forge/verification.py": "41cc90bb058f27d8f1870e0b22de8dcd7857c2add152af8fee04b8cedb956c24",
    "forge/verification_allowance.py": "a380e9271095e919b98ecd24d77e68e01e252897e613190810db6e09ef050c28",
    "forge/workflows.py": "6140faef406ca2b3ea9d7b23c25b09f2fe096f6fb153660d1b6e8275451113fa"
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


class HostStop(BaseException):
    """SIGTERM from the host; unlike SystemExit, in-process gates cannot record it as a result."""


def run_program() -> int:
    """Run main() as a program, unwinding on SIGTERM so execute() stops its own child group."""
    def stop(signum: int, _frame: object) -> None:
        raise HostStop(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    try:
        return main()
    except HostStop as stopped:
        return stopped.args[0]


if __name__ == "__main__":
    sys.exit(run_program())
