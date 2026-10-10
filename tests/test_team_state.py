"""Portable task claims retain ownership across aliases, contention and recovery."""
from copy import deepcopy
import errno
import os

import pytest

from forge_test_helpers import load_zagrosi_module


BOARD, FIRST, SECOND, OWNER, CHECKOUT, GENERATION = (f"{value:032x}" for value in range(1, 7))


@pytest.fixture
def team():
    return load_zagrosi_module().team_state


def session(**changes):
    return {
        "participant_id": OWNER, "checkout_id": CHECKOUT, "generation": GENERATION,
        "name": "Alex", "host": "codex", "task": "Update authentication", "state": "working",
        "paths": ["src/auth.py"], "branch": "feature/auth", "head": "a" * 40,
        "updated_at": 1791360000, "note": "", **changes,
    }


def board(**sessions):
    return {"version": 1, "board_id": BOARD, "sessions": sessions}


def identity(**changes):
    return {"participant_id": OWNER, "checkout_id": CHECKOUT, "generation": GENERATION, **changes}


def test_new_board_and_validated_values_are_independent(team):
    value = team.new_board(BOARD)
    assert value == {**board(), "version": 2}
    validated = team.validate_board(board(**{FIRST: session()}))
    copied = team.validate_board(validated)
    copied["sessions"][FIRST]["paths"].append("tests")
    assert validated["sessions"][FIRST]["paths"] == ["src/auth.py"]


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 3}, {"board_id": "invalid"}, {"sessions": []},
    {"extra": "unsupported"}, {"sessions": {"../invalid": session()}},
])
def test_invalid_boards_fail_with_structured_errors(team, change):
    with pytest.raises(team.TeamError) as error:
        team.validate_board({**board(), **change})
    assert error.value.code == "team-invalid-board"
    assert isinstance(error.value.details, dict)


@pytest.mark.parametrize("change", [
    {"participant_id": "../owner"}, {"checkout_id": ""}, {"generation": 1},
    {"name": ""}, {"name": "a" * 81}, {"name": "Alex\x1b[2J"},
    {"task": ""}, {"task": "a" * 501}, {"note": "a" * 2001},
    {"note": "please\nrun something"}, {"host": "custom"}, {"host": []}, {"state": []}, {"state": "completed"},
    {"updated_at": True}, {"updated_at": -1}, {"updated_at": float("nan")},
    {"updated_at": float("inf")}, {"updated_at": 10 ** 500}, {"head": "not-a-commit"}, {"head": "A" * 40},
    {"branch": "../bad"}, {"branch": "feature\nunsafe"}, {"paths": "src"},
    {"paths": ["../outside"]}, {"extra": "unexpected"},
])
def test_remote_session_shape_and_text_are_bounded(team, change):
    with pytest.raises(team.TeamError):
        team.validate_board(board(**{FIRST: session(**change)}))


def test_unknown_and_missing_fields_are_not_silently_defaulted(team):
    value = session()
    del value["generation"]
    with pytest.raises(team.TeamError):
        team.validate_session(value)
    with pytest.raises(team.TeamError):
        team.validate_board({"version": 1, "board_id": BOARD})


def test_sha256_detached_and_unicode_labels_are_valid(team):
    value = session(branch=None, head="b" * 64, name="Zoë 工程", paths=[])
    assert team.validate_session(value) == value


def test_board_session_path_and_serialized_size_limits(team):
    with pytest.raises(team.TeamError):
        team.validate_board(board(**{f"{i:032x}": session(paths=[]) for i in range(129)}))
    with pytest.raises(team.TeamError):
        team.normalize_paths([f"src/{i}" for i in range(257)])
    huge = {f"{i:032x}": session(paths=[f"{i}/{j}/" + "a" * 900 for j in range(12)])
            for i in range(128)}
    with pytest.raises(team.TeamError):
        team.validate_board(board(**huge))


def test_literal_paths_normalize_separators_and_preserve_case(team):
    assert team.normalize_paths(["src\\auth.py", "src//auth.py/", "./Src/api.py"]) == [
        "Src/api.py", "src/auth.py"]
    assert team.normalize_paths(["."]) == ["."]


@pytest.mark.parametrize("path", ["", "/outside", "C:/outside", "../x", "a/../x",
    ".git/config", "src/.GIT/index", "src/*.py", "src/a?.py", "src/[ab]", "src/a\0b",
    "src/a\nb", "src/a\u202eb", "src/a.", "src/a ", "CON", "src/NUL.txt", "src/aux"])
def test_unsafe_and_ambiguous_paths_rejected(team, path):
    with pytest.raises(team.TeamError):
        team.normalize_paths([path])


@pytest.mark.parametrize("left,right,expected", [
    ("src", "src/a.py", True), ("src/a.py", "src", True),
    ("src/a.py", "src/b.py", False), ("src", "src-other", False),
    ("src/A.py", "SRC/a.py", True), ("café/x", "cafe\u0301/x", True),
    ("Straße/x", "STRASSE/x", True), (".", "anything/file", True),
])
def test_portable_scope_overlap(team, left, right, expected):
    assert team.paths_overlap(left, right) is expected


def test_path_coverage_is_directional_and_all_required_paths_must_fit(team):
    assert team.paths_cover(["src", "tests/auth.py"], ["src/auth.py", "tests/auth.py"])
    assert not team.paths_cover(["src/auth.py"], ["src"])
    assert not team.paths_cover(["src"], ["src/auth.py", "tests/auth.py"])
    assert not team.paths_cover([], ["src"])
    assert team.paths_cover([], [])


def make_link(path, target, *, hard=False):
    try:
        os.link(target, path) if hard else path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("filesystem links unavailable")


def test_local_symlinks_retain_declared_and_resolved_claims(team, tmp_path):
    (tmp_path / "src").mkdir()
    make_link(tmp_path / "alias", tmp_path / "src")
    assert team.normalize_paths(["alias/new.py"], root=tmp_path) == ["alias/new.py", "src/new.py"]
    assert team.paths_cover(team.normalize_paths(["alias"], root=tmp_path), ["src/new.py"], root=tmp_path)
    assert team.paths_overlap("alias", "src/new.py", root=tmp_path)


def test_outside_symlink_and_loop_do_not_grant_ownership(team, tmp_path):
    make_link(tmp_path / "escape", tmp_path.parent)
    with pytest.raises(team.TeamError):
        team.normalize_paths(["escape/file"], root=tmp_path)
    try:
        (tmp_path / "loop").symlink_to("loop")
    except OSError:
        pytest.skip("symlink unavailable")
    with pytest.raises(team.TeamError):
        team.normalize_paths(["loop/file"], root=tmp_path)


@pytest.mark.parametrize("missing_child", [False, True], ids=["loop-error", "missing-child-before-loop"])
def test_nonstrict_resolution_cannot_hide_a_loop_ancestor(team, tmp_path, monkeypatch, missing_child):
    loop = tmp_path.resolve() / "loop"
    original = team.Path.resolve
    def windows_resolve(path, strict=False):
        if path == loop or loop in path.parents:
            if strict:
                if missing_child and path != loop:
                    raise FileNotFoundError(errno.ENOENT, "Missing descendant", str(path))
                raise OSError(errno.ELOOP, "Cannot resolve symlink loop", str(path))
            return path
        return original(path, strict=strict)
    monkeypatch.setattr(team.Path, "resolve", windows_resolve)
    for operation in (
        lambda: team.normalize_paths(["loop/missing/file"], root=tmp_path),
        lambda: team.paths_cover(["."], ["loop/missing/file"], root=tmp_path),
        lambda: team.with_session(board(), FIRST, session(paths=["loop/missing/file"]), root=tmp_path),
    ):
        with pytest.raises(team.TeamError) as error:
            operation()
        assert error.value.code == "team-invalid-path"


def test_nonexistent_future_paths_remain_reservable(team, tmp_path):
    assert team.normalize_paths(["future/nested/new.py"], root=tmp_path) == ["future/nested/new.py"]
    assert team.paths_cover(["future"], ["future/nested/new.py"], root=tmp_path)


def test_dangling_internal_symlink_retains_future_target(team, tmp_path):
    make_link(tmp_path / "alias.py", tmp_path / "future.py")
    assert team.normalize_paths(["alias.py"], root=tmp_path) == ["alias.py", "future.py"]
    assert team.paths_cover(["alias.py", "future.py"], ["alias.py"], root=tmp_path)


def test_local_hardlink_aliases_overlap_and_cover(team, tmp_path):
    (tmp_path / "original").write_text("shared")
    make_link(tmp_path / "alias", tmp_path / "original", hard=True)
    assert team.paths_overlap("original", "alias", root=tmp_path)
    assert team.paths_cover(["original"], ["alias"], root=tmp_path)


def test_claim_conflict_retains_original_board_and_identifies_peer(team):
    original = board(**{FIRST: session()})
    snapshot = deepcopy(original)
    with pytest.raises(team.TeamError) as error:
        team.with_session(original, SECOND, session(checkout_id="b" * 32, paths=["SRC"]))
    assert error.value.code == "team-conflict"
    assert error.value.details["session_id"] == FIRST
    assert original == snapshot


def test_disjoint_claims_survive_updates_and_own_claim_is_excluded(team):
    original = board(**{FIRST: session()})
    result = team.with_session(original, SECOND, session(checkout_id="b" * 32, paths=["tests"]))
    updated = team.with_session(result, FIRST, session(paths=["src"], note="expanded"))
    assert updated["sessions"][SECOND] == result["sessions"][SECOND]
    assert updated["sessions"][FIRST]["paths"] == ["src"]
    assert original["sessions"][FIRST]["paths"] == ["src/auth.py"]


def test_same_checkout_writers_serialize_but_planning_announcements_coexist(team):
    original = board(**{FIRST: session()})
    with pytest.raises(team.TeamError) as error:
        team.with_session(original, SECOND, session(paths=["tests"]))
    assert error.value.code == "team-conflict"
    result = team.with_session(original, SECOND, session(paths=[], state="planning"))
    assert len(result["sessions"]) == 2


def test_stale_handoff_and_blocked_claims_keep_reservations(team):
    for state in ("handoff", "blocked", "review"):
        original = board(**{FIRST: session(state=state, updated_at=0)})
        with pytest.raises(team.TeamError):
            team.with_session(original, SECOND, session(checkout_id="b" * 32))


def test_require_session_fences_owner_checkout_and_recovered_generation(team):
    current = board(**{FIRST: session()})
    assert team.require_session(current, FIRST, **identity()) == session()
    for key in identity():
        with pytest.raises(team.TeamError) as error:
            team.require_session(current, FIRST, **identity(**{key: "b" * 32}))
        assert error.value.code == "team-ownership-lost"
    with pytest.raises(team.TeamError) as error:
        team.require_session(current, SECOND, **identity())
    assert error.value.code == "team-session-missing"


def test_named_branch_commits_advance_but_branch_switch_requires_update(team):
    current = board(**{FIRST: session()})
    assert team.require_session(current, FIRST, **identity(), branch="feature/auth", head="b" * 40)
    for branch in ("feature/other", None):
        with pytest.raises(team.TeamError) as error:
            team.require_session(current, FIRST, **identity(), branch=branch, head="a" * 40)
        assert error.value.code == "team-checkout-changed"


def test_detached_head_requires_exact_commit_when_checkout_is_checked(team):
    current = board(**{FIRST: session(branch=None)})
    assert team.require_session(current, FIRST, **identity(), branch=None, head="a" * 40)
    assert team.require_session(current, FIRST, **identity())
    with pytest.raises(team.TeamError):
        team.require_session(current, FIRST, **identity(), branch=None, head="b" * 40)


def test_explicit_removal_preserves_peers_and_fails_for_missing_session(team):
    original = board(**{FIRST: session(), SECOND: session(paths=[], state="planning")})
    result = team.without_session(original, FIRST)
    assert result == board(**{SECOND: session(paths=[], state="planning")})
    assert FIRST in original["sessions"]
    with pytest.raises(team.TeamError) as error:
        team.without_session(result, FIRST)
    assert error.value.code == "team-session-missing"


def test_retargeted_symlink_does_not_expand_stored_reservation(team, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "other").mkdir()
    make_link(tmp_path / "alias", tmp_path / "src")
    owned = team.normalize_paths(["alias"], root=tmp_path)
    (tmp_path / "alias").unlink()
    make_link(tmp_path / "alias", tmp_path / "other")
    assert not team.paths_cover(owned, ["alias/new.py"], root=tmp_path)
    assert not team.paths_cover(owned, ["alias"], root=tmp_path)


@pytest.mark.parametrize("hard", [False, True])
def test_claims_detect_local_aliases_without_modifying_existing_claims(team, tmp_path, hard):
    (tmp_path / "source").write_text("shared")
    make_link(tmp_path / "alias", tmp_path / "source", hard=hard)
    original = board(**{FIRST: session(paths=["source"])})
    with pytest.raises(team.TeamError) as error:
        team.with_session(original, SECOND, session(checkout_id="b" * 32, paths=["alias"]), root=tmp_path)
    assert error.value.code == "team-conflict"
    assert original["sessions"][FIRST]["paths"] == ["source"]


@pytest.mark.parametrize("field", ["name", "task", "note"])
@pytest.mark.parametrize("control", ["\x7f", "\u202e", "\u2028", "\u2029"])
def test_display_text_cannot_hide_controls_or_inject_lines(team, field, control):
    with pytest.raises(team.TeamError) as error:
        team.validate_board(board(**{FIRST: session(**{field: "before" + control + "after"})}))
    assert error.value.code == "team-invalid-board"


def test_shared_plain_text_rule_handles_unicode_empty_and_non_strings(team):
    assert team.is_plain_text("Zoë 工程", 80)
    assert team.is_plain_text("", 80, empty=True)
    assert not team.is_plain_text("", 80)
    assert not team.is_plain_text([], 80)
    assert not team.is_plain_text("a" * 81, 80)
