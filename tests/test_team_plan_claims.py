"""Prepared contract readers coexist while retaining ordinary write ownership."""
from copy import deepcopy

import pytest

from test_team_state import FIRST, SECOND, board, session, team


def plan(**changes):
    return {"path": ".forge/plans/auth", "digest": "d" * 64,
            "commit": "a" * 40, "section": "section-01-api", **changes}


def reader(**changes):
    return session(plan=plan(), **changes)


def peer(**changes):
    return session(checkout_id="9" * 32, participant_id="8" * 32,
                   paths=["src/ui.py"], **changes)


def test_distinct_sections_share_one_contract_across_source_commits(team):
    value = board(**{FIRST: reader()})
    other = peer(plan=plan(commit="b" * 64, section="section-02-ui"))
    result = team.with_session(value, SECOND, other)
    assert result["sessions"][FIRST]["plan"]["section"] == "section-01-api"
    assert result["sessions"][SECOND]["plan"] == other["plan"]
    assert value == board(**{FIRST: reader()})


def test_copied_plan_descriptors_do_not_mutate_board(team):
    value = board(**{FIRST: reader()})
    validated = team.validate_board(value)
    validated["sessions"][FIRST]["plan"]["section"] = None
    assert value["sessions"][FIRST]["plan"]["section"] == "section-01-api"


@pytest.mark.parametrize("path", [".forge/plans/auth", ".FORGE/plans/AUTH", ".forge/plans/auth/subplan"])
def test_mixed_contract_versions_are_rejected_without_mutation(team, path):
    value = board(**{FIRST: reader()})
    before = deepcopy(value)
    with pytest.raises(team.TeamError) as failed:
        team.with_session(value, SECOND, peer(plan=plan(path=path, digest="e" * 64)))
    assert failed.value.code == "team-conflict"
    assert failed.value.details["conflict"] == "plan-revision"
    assert value == before


@pytest.mark.parametrize("writer_first", [False, True])
@pytest.mark.parametrize("path", [".", ".forge", ".forge/plans/auth/spec.md", ".FORGE/plans/AUTH/sections"])
def test_contract_writes_conflict_with_empty_path_readers(team, writer_first, path):
    read = reader(paths=[], state="planning")
    write = peer()
    write["paths"] = [path]
    initial, added = (write, read) if writer_first else (read, write)
    with pytest.raises(team.TeamError) as failed:
        team.with_session(board(**{FIRST: initial}), SECOND, added)
    assert failed.value.code == "team-conflict"
    assert failed.value.details["conflict"] == "plan-write"


def test_a_prepared_task_cannot_reserve_writes_to_its_own_frozen_contract(team):
    with pytest.raises(team.TeamError) as failed:
        team.with_session(board(), FIRST, reader(paths=[".forge/plans/auth/spec.md"]))
    assert failed.value.details["conflict"] == "plan-write"


def test_unrelated_plans_and_legacy_writers_remain_compatible(team):
    first = reader()
    second = peer(plan=plan(path=".forge/plans/billing", digest="e" * 64))
    combined = team.with_session(board(**{FIRST: first}), SECOND, second)
    combined = team.without_session(combined, SECOND)
    assert team.with_session(combined, SECOND, peer())["sessions"][SECOND] == peer()


def test_same_contract_readers_still_cannot_share_a_writing_checkout(team):
    with pytest.raises(team.TeamError) as failed:
        team.with_session(board(**{FIRST: reader()}), SECOND,
                          reader(paths=["src/ui.py"]))
    assert failed.value.details["conflict"] == "checkout"


@pytest.mark.parametrize("descriptor", [
    None, {}, {**plan(), "extra": True}, plan(path="."), plan(path="../private"),
    plan(path=".git/config"), plan(digest="bad"), plan(commit="bad"),
    plan(section="../../private"), plan(section=[]),
])
def test_invalid_plan_descriptors_fail_closed(team, descriptor):
    with pytest.raises(team.TeamError):
        team.validate_board(board(**{FIRST: reader() | {"plan": descriptor}}))


def test_contract_alias_cannot_bypass_writer_conflicts(team, tmp_path):
    canonical = tmp_path / ".forge/plans/auth"
    canonical.mkdir(parents=True)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(canonical, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    write = peer()
    write["paths"] = ["alias/spec.md"]
    with pytest.raises(team.TeamError) as failed:
        team.with_session(board(**{FIRST: reader(paths=[])}), SECOND, write, root=tmp_path)
    assert failed.value.details["conflict"] == "plan-write"
