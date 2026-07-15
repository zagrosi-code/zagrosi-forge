from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from codex_cachebuster_support import CachebusterProbe, run_cachebuster_probe


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "spike-marketplace"
ROOT = Path(__file__).parents[2]
EXPECTED_SKILLS = (
    "zagrosi-implement",
    "zagrosi-plan",
    "zagrosi-project",
)


@pytest.fixture(scope="module")
def native_probe(tmp_path_factory: pytest.TempPathFactory) -> CachebusterProbe:
    return run_cachebuster_probe(
        work_root=tmp_path_factory.mktemp("codex-cachebuster"),
        fixture_root=FIXTURE_ROOT,
    )


def test_codex_accepts_generated_marketplace_shape(
    native_probe: CachebusterProbe,
) -> None:
    source_marketplace = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    source_marketplace["name"] = "zagrosi-spike"
    source_marketplace["interface"]["displayName"] = "Zagrosi Spike"
    source_marketplace["plugins"][0]["source"]["path"] = "./plugins/zagrosi-forge"
    fixed_marketplace = json.loads(
        (FIXTURE_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    assert fixed_marketplace == source_marketplace
    assert native_probe.codex_version == "0.144.4"
    assert native_probe.tool_source in {"declared", "path", "acquired"}
    assert re.fullmatch(r"[0-9a-f]{64}", native_probe.locked_artifact_sha256)
    assert len(native_probe.candidates) == 2
    for candidate in native_probe.candidates:
        assert candidate.marketplace_name == "zagrosi-spike"
        assert candidate.marketplace_listed
        assert candidate.plugin_available
        assert candidate.plugin_installed


def test_codex_discovers_exactly_three_forge_skills(
    native_probe: CachebusterProbe,
) -> None:
    for candidate in native_probe.candidates:
        assert candidate.discovered_skills == EXPECTED_SKILLS
        assert candidate.prompt_marker == candidate.marker


def test_codex_selects_each_fixed_digest_derived_candidate(
    native_probe: CachebusterProbe,
) -> None:
    first, second = native_probe.candidates
    assert first.base_version == second.base_version == "0.2.0"
    assert first.isolated_codex_home == second.isolated_codex_home
    assert first.base_payload_digest != second.base_payload_digest
    assert first.install_version != second.install_version
    for candidate in native_probe.candidates:
        assert re.fullmatch(
            r"0\.2\.0\+codex\.local-[0-9a-f]{32}", candidate.install_version
        )
        assert candidate.install_version.endswith(candidate.base_payload_digest[:32])
        assert candidate.selected_version == candidate.install_version
        assert candidate.cache_marker == candidate.marker


def test_codex_cache_equals_rendered_plugin_manifest(
    native_probe: CachebusterProbe,
) -> None:
    for candidate in native_probe.candidates:
        assert candidate.cache_root.is_relative_to(candidate.isolated_codex_home)
        assert candidate.cache_manifest == candidate.rendered_manifest


def test_codex_rejects_or_ignores_unsupported_manifest_field_consistently(
    native_probe: CachebusterProbe,
) -> None:
    outcomes = {
        candidate.scripts_field_outcome for candidate in native_probe.candidates
    }
    assert outcomes == {"accepted_no_execution_observed"}


def test_native_profile_isolation_preserves_real_home(
    native_probe: CachebusterProbe,
) -> None:
    assert native_probe.sentinel_bytes_unchanged
    assert native_probe.sentinel_identity_unchanged
    for candidate in native_probe.candidates:
        assert candidate.all_profile_variables_isolated
        assert candidate.isolated_codex_home.is_relative_to(candidate.isolated_root)
