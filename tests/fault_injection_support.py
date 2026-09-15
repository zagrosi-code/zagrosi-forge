from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

from detached_test_support import (
    replace_file,
)
from forge_test_helpers import (
    IMPLEMENTATION_SOURCE_RELATIVE_PATHS,
    ROOT,
)


def instrument_runtime_modules(
    plugin_root: Path,
    module_names: tuple[str, ...],
    insertion: str,
    replacements: dict[str, str],
) -> Path:
    """Keep crash boundaries exact while targeting their extracted source modules."""
    symbol_owners = {
        "secure_io": ("_write_all", "load_canonical_json_at", "write_canonical_json_at", "read_single_link_regular_at"),
        "transaction_io": ("write_new_fixed_file_at", "rename_fixed_file_no_replace_at", "section_record_entry_stat", "install_staged_section_pinner", "replace_state_from_transaction", "unlink_fixed_file_at"),
        "detached_contract": ("DETACHED_JSON_CAP", "SECTION_RECORD_TRANSACTION_DIR"),
        "locks": ("detached_global_lock",),
        "storage": ("absolute_path_no_follow",),
    }

    def qualify(fragment: str, module: str) -> str:
        for owner, symbols in symbol_owners.items():
            if owner != module:
                for symbol in symbols:
                    fragment = re.sub(rf"(?<![\w.]){symbol}\b", f"_{owner}.{symbol}", fragment)
        return fragment

    texts = {name: (plugin_root / "scripts/forge" / f"{name}.py").read_text() for name in module_names}
    changed = set()
    for needle, replacement in replacements.items():
        matches = []
        for name, text in texts.items():
            lines = textwrap.dedent(qualify(needle, name)).splitlines(keepends=True)
            pattern = r"(?m)^(?P<indent> *)" + r"(?P=indent)".join(map(re.escape, lines))
            matches.extend((name, match) for match in re.finditer(pattern, text))
        original_indent = len(needle.splitlines()[0]) - len(textwrap.dedent(needle).splitlines()[0])
        exact_indent = [(name, match) for name, match in matches if len(match["indent"]) == original_indent]
        matches = exact_indent or matches
        assert len(matches) == 1, needle
        name, match = matches[0]
        injected = textwrap.indent(textwrap.dedent(qualify(replacement, name)), match["indent"])
        texts[name] = texts[name][:match.start()] + injected + texts[name][match.end():]
        changed.add(name)
    for name in changed:
        path = plugin_root / "scripts/forge" / f"{name}.py"
        anchor = "from __future__ import annotations\n"
        helper = "\nimport os\nimport signal\nimport time\nfrom pathlib import Path\n" + qualify(insertion, name)
        assert texts[name].count(anchor) == 1
        text = texts[name].replace(anchor, anchor + helper, 1)
        compile(text, str(path), "exec")
        replace_file(path, text.encode(), mode=path.stat().st_mode & 0o777)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/update_runtime_manifest.py"), "--plugin-root", str(plugin_root)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return plugin_root / IMPLEMENTATION_SOURCE_RELATIVE_PATHS["tool"]


def instrument_record_crashpoints(plugin_root: Path) -> Path:
    insertion = '''\n\ndef _test_record_crashpoint(name: str) -> None:\n    if name == "state-cas-fsync" and os.environ.get("ZAGROSI_TEST_FORCE_ROLLBACK") == "1":\n        Path(os.environ["ZAGROSI_TEST_FORCE_ROLLBACK_PATH"]).write_bytes(b'{"schema":"test-record-gate-v1","verdict":"DRIFT"}\\n')\n    requested = os.environ.get("ZAGROSI_TEST_RECORD_CRASHPOINT")\n    if requested == name:\n        os.kill(os.getpid(), signal.SIGKILL)\n    pausepoint = os.environ.get("ZAGROSI_TEST_RECORD_PAUSEPOINT")\n    if pausepoint == name:\n        ready = Path(os.environ["ZAGROSI_TEST_RECORD_READY"])\n        release = Path(os.environ["ZAGROSI_TEST_RECORD_RELEASE"])\n        ready.write_text("ready\\n")\n        while not release.exists():\n            time.sleep(0.01)\n\ndef _test_precreate_adopted_pinner(root_fd: int, transaction_fd: int, pinner_path: str) -> None:\n    mode = os.environ.get("ZAGROSI_TEST_RECORD_ADOPT_PREEXISTING")\n    if mode not in {"1", "wrong"}:\n        return\n    staged, raw = load_canonical_json_at(transaction_fd, "pinner.json")\n    published = staged if mode == "1" else {**staged, "notes": "wrong preexisting bytes"}\n    write_canonical_json_at(root_fd, pinner_path, published, immutable=True)\n    reopened = read_single_link_regular_at(root_fd, pinner_path, cap=DETACHED_JSON_CAP, require_mode=0o600)\n    if mode == "1" and reopened != raw:\n        raise AssertionError("adopted test pinner bytes changed")\n'''
    replacements = {
        '        _write_all(file_fd, raw)\n'
        '        os.fsync(file_fd)\n': (
            '        partial_crashpoint = {\n'
            '            "pinner.tmp": "pinner-tmp-partial",\n'
            '            "transaction.write.tmp": "journal-write-temp-partial",\n'
            '        }.get(name)\n'
            '        if (\n'
            '            partial_crashpoint is not None\n'
            '            and partial_crashpoint == os.environ.get("ZAGROSI_TEST_RECORD_CRASHPOINT")\n'
            '        ):\n'
            '            _write_all(file_fd, raw[: max(1, len(raw) // 2)])\n'
            '            os.fsync(file_fd)\n'
            '            _test_record_crashpoint(partial_crashpoint)\n'
            '        _write_all(file_fd, raw)\n'
            '        os.fsync(file_fd)\n'
        ),
        '    os.replace(\n'
        '        "transaction.json",\n'
        '        "rollback.json",\n'
        '        src_dir_fd=transaction_fd,\n'
        '        dst_dir_fd=transaction_fd,\n'
        '    )\n'
        '    os.fsync(transaction_fd)\n': (
            '    os.replace(\n'
            '        "transaction.json",\n'
            '        "rollback.json",\n'
            '        src_dir_fd=transaction_fd,\n'
            '        dst_dir_fd=transaction_fd,\n'
            '    )\n'
            '    _test_record_crashpoint("rollback-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("rollback-rename-fsync")\n'
        ),
        '        os.unlink(parts[1], dir_fd=pinners_fd)\n'
        '        os.fsync(pinners_fd)\n': (
            '        os.unlink(parts[1], dir_fd=pinners_fd)\n'
            '        os.fsync(pinners_fd)\n'
            '        _test_record_crashpoint("rollback-final-delete-fsync")\n'
        ),
        '        write_new_fixed_file_at(transaction_fd, "state.json", base_raw)\n': (
            '        write_new_fixed_file_at(transaction_fd, "state.json", base_raw)\n'
            '        _test_record_crashpoint("rollback-state-temp-fsync")\n'
        ),
        '    if section_record_entry_stat(transaction_fd, "state.json") is None:\n'
        '        write_new_fixed_file_at(transaction_fd, "state.json", replacement_raw)\n': (
            '    if section_record_entry_stat(transaction_fd, "state.json") is None:\n'
            '        write_new_fixed_file_at(transaction_fd, "state.json", replacement_raw)\n'
            '        _test_record_crashpoint("forward-state-temp-fsync")\n'
        ),
        '    os.replace("state.json", "zagrosi_implement_state.json", src_dir_fd=transaction_fd, dst_dir_fd=root_fd)\n'
        '    os.fsync(root_fd)\n': (
            '    os.replace("state.json", "zagrosi_implement_state.json", src_dir_fd=transaction_fd, dst_dir_fd=root_fd)\n'
            '    _test_record_crashpoint("forward-state-replace-before-root-fsync")\n'
            '    os.fsync(root_fd)\n'
        ),
        '    os.replace(\n'
        '        "state.json",\n'
        '        "zagrosi_implement_state.json",\n'
        '        src_dir_fd=transaction_fd,\n'
        '        dst_dir_fd=root_fd,\n'
        '    )\n'
        '    os.fsync(root_fd)\n': (
            '    os.replace(\n'
            '        "state.json",\n'
            '        "zagrosi_implement_state.json",\n'
            '        src_dir_fd=transaction_fd,\n'
            '        dst_dir_fd=root_fd,\n'
            '    )\n'
            '    _test_record_crashpoint("rollback-state-replace-before-root-fsync")\n'
            '    os.fsync(root_fd)\n'
            '    _test_record_crashpoint("rollback-state-replace-fsync")\n'
        ),
        '    os.unlink("rollback.json", dir_fd=transaction_fd)\n'
        '    os.fsync(transaction_fd)\n': (
            '    os.unlink("rollback.json", dir_fd=transaction_fd)\n'
            '    _test_record_crashpoint("rollback-unlink-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("rollback-unlink-fsync")\n'
        ),
        '    write_new_fixed_file_at(transaction_fd, "transaction.write.tmp", raw)\n'
        '    rename_fixed_file_no_replace_at(\n'
        '        transaction_fd,\n'
        '        "transaction.write.tmp",\n'
        '        "transaction.tmp",\n'
        '    )\n'
        '    os.fsync(transaction_fd)\n': (
            '    write_new_fixed_file_at(transaction_fd, "transaction.write.tmp", raw)\n'
            '    _test_record_crashpoint("journal-write-temp-fsync")\n'
            '    rename_fixed_file_no_replace_at(\n'
            '        transaction_fd,\n'
            '        "transaction.write.tmp",\n'
            '        "transaction.tmp",\n'
            '    )\n'
            '    _test_record_crashpoint("journal-temp-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("journal-temp-rename-fsync")\n'
        ),
        '    rename_fixed_file_no_replace_at(\n'
        '        transaction_fd,\n'
        '        "transaction.tmp",\n'
        '        "transaction.json",\n'
        '    )\n'
        '    os.fsync(transaction_fd)\n': (
            '    rename_fixed_file_no_replace_at(\n'
            '        transaction_fd,\n'
            '        "transaction.tmp",\n'
            '        "transaction.json",\n'
            '    )\n'
            '    _test_record_crashpoint("journal-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("journal-rename-fsync")\n'
        ),
        '    write_new_fixed_file_at(transaction_fd, "pinner.tmp", pinner_raw)\n'
        '    rename_fixed_file_no_replace_at(transaction_fd, "pinner.tmp", "pinner.json")\n'
        '    os.fsync(transaction_fd)\n': (
            '    write_new_fixed_file_at(transaction_fd, "pinner.tmp", pinner_raw)\n'
            '    _test_record_crashpoint("pinner-tmp-write-fsync")\n'
            '    rename_fixed_file_no_replace_at(transaction_fd, "pinner.tmp", "pinner.json")\n'
            '    _test_record_crashpoint("pinner-rename-before-dir-fsync")\n'
            '    os.fsync(transaction_fd)\n'
            '    _test_record_crashpoint("pinner-rename-dir-fsync")\n'
        ),
        '            install_staged_section_pinner(root_fd, transaction_fd, pinner_path, pinner_raw)\n': (
        '            _test_precreate_adopted_pinner(root_fd, transaction_fd, pinner_path)\n'
        '            install_staged_section_pinner(root_fd, transaction_fd, pinner_path, pinner_raw)\n'
        '            _test_record_crashpoint("final-link-fsync")\n'
        ),
        '            created = True\n'
        '            os.fsync(pinners_fd)\n': (
            '            created = True\n'
            '            _test_record_crashpoint("final-link-before-dir-fsync")\n'
            '            os.fsync(pinners_fd)\n'
        ),
        '            replace_state_from_transaction(root_fd, transaction_fd, base_state_raw, candidate_state)\n': (
            '            replace_state_from_transaction(root_fd, transaction_fd, base_state_raw, candidate_state)\n'
            '            _test_record_crashpoint("state-cas-fsync")\n'
        ),
        '                candidate_state_raw,\n                require_lock_authority,\n            )\n            require_lock_authority()\n': (
            '                candidate_state_raw,\n                require_lock_authority,\n            )\n'
            '            _test_record_crashpoint("post-state-validation")\n'
            '            require_lock_authority()\n'
        ),
        '        os.fsync(transaction_fd)\n    except OSError:\n        if not journal_removed:\n': (
            '        os.fsync(transaction_fd)\n'
            '        _test_record_crashpoint("journal-unlink-fsync")\n'
            '    except OSError:\n        if not journal_removed:\n'
        ),
        '        os.unlink("transaction.json", dir_fd=transaction_fd)\n'
        '        journal_removed = True\n'
        '        os.fsync(transaction_fd)\n': (
            '        os.unlink("transaction.json", dir_fd=transaction_fd)\n'
            '        journal_removed = True\n'
            '        _test_record_crashpoint("journal-unlink-before-dir-fsync")\n'
            '        os.fsync(transaction_fd)\n'
        ),
        '            unlink_fixed_file_at(transaction_fd, name, missing_ok=True)\n': (
            '            unlink_fixed_file_at(transaction_fd, name, missing_ok=True)\n'
            '            if name == "pinner.json":\n'
            '                _test_record_crashpoint("stage-cleanup-fsync")\n'
        ),
        '        os.rmdir(Path(SECTION_RECORD_TRANSACTION_DIR).name, dir_fd=pinners_fd)\n        os.fsync(pinners_fd)\n': (
            '        os.rmdir(Path(SECTION_RECORD_TRANSACTION_DIR).name, dir_fd=pinners_fd)\n'
            '        _test_record_crashpoint("transaction-rmdir-before-parent-fsync")\n'
            '        os.fsync(pinners_fd)\n'
        ),
    }
    return instrument_runtime_modules(plugin_root, ("transaction_io", "detached_record"), insertion, replacements)


def instrument_root_lifecycle_points(plugin_root: Path) -> Path:
    insertion = '''\n\ndef _test_root_lifecycle_point(name: str) -> None:\n    if os.environ.get("ZAGROSI_TEST_ROOT_CRASHPOINT") == name:\n        os.kill(os.getpid(), signal.SIGKILL)\n    if os.environ.get("ZAGROSI_TEST_ROOT_PAUSEPOINT") == name:\n        ready = Path(os.environ["ZAGROSI_TEST_ROOT_READY"])\n        release = Path(os.environ["ZAGROSI_TEST_ROOT_RELEASE"])\n        ready.write_text("ready\\n")\n        while not release.exists():\n            time.sleep(0.01)\n'''
    replacements = {
        '                os.fsync(temporary_fd)\n                os.close(temporary_fd)\n': (
            '                os.fsync(temporary_fd)\n'
            '                _test_root_lifecycle_point(f"canonical-temp-fsync:{relative}")\n'
            '                os.close(temporary_fd)\n'
        ),
        '        _write_all(file_fd, pending_raw)\n        os.fsync(file_fd)\n        os.close(file_fd)\n': (
            '        _write_all(file_fd, pending_raw)\n'
            '        os.fsync(file_fd)\n'
            '        _test_root_lifecycle_point(f"setup-slot-temp-fsync:{relative}")\n'
            '        os.close(file_fd)\n'
        ),
        '        require_global_authority = lock_context.enter_context(detached_global_lock(lock_deadline))\n'
        '        require_global_authority()\n'
        '        requested_root = absolute_path_no_follow(args.implementation_root)\n': (
            '        require_global_authority = lock_context.enter_context(detached_global_lock(lock_deadline))\n'
            '        require_global_authority()\n'
            '        _test_root_lifecycle_point("setup-global-acquired")\n'
            '        requested_root = absolute_path_no_follow(args.implementation_root)\n'
        ),
    }
    return instrument_runtime_modules(plugin_root, ("secure_io", "detached_state", "detached_setup"), insertion, replacements)
