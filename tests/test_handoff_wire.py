from __future__ import annotations

import json

import pytest
from detached_test_support import (
    domain_sha256_for_test,
)
from forge_test_helpers import (
    load_zagrosi_module,
)


def test_handoff_canonical_json_uses_nfc_cj0_for_digests_and_lf_only_for_wire() -> None:
    module = load_zagrosi_module()
    payload = {"b": "e\u0301", "a": 1}
    expected_body = b'{"a":1,"b":"\xc3\xa9"}'

    assert module.handoff_wire.handoff_canonical_json_body(payload) == expected_body
    assert module.handoff_wire.handoff_canonical_json_bytes(payload) == expected_body + b"\n"
    assert module.handoff_wire.domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-self\0",
        module.handoff_wire.handoff_canonical_json_body(payload),
    ) == "sha256:1e3c3029b017c7ce4e9a64b2d5bed91dfa261e203b918776080420ff73f4e778"
    assert module.handoff_wire.domain_sha256(
        b"unit12-privileged-darwin-apfs-gate-handoff-request-v1-final-wire\0",
        module.handoff_wire.handoff_canonical_json_body(payload),
    ) == "sha256:ace49ee7e45ee0b2fe30ad19c96159384e5bc981aa809b9405fc9528fa766d4d"
    with pytest.raises(module.models.DetachedImplementationError, match="canonical JSON"):
        module.handoff_wire.parse_canonical_object_bytes(
            b'{"a":1,"b":"e\xcc\x81"}\n',
            cap=4096,
            label="NFD mutant",
        )


def test_detached_root_identity_digest_vector_uses_cj0_without_lf() -> None:
    identity = {"device": 1, "gid": 20, "inode": 2, "link_count": 5, "mode": 448, "uid": 501}
    body = b'{"device":1,"gid":20,"inode":2,"link_count":5,"mode":448,"uid":501}'
    domain = b"zagrosi-detached-implementation-root-identity-v1\0"

    assert domain_sha256_for_test(domain, body) == (
        "sha256:f695b6afe7f1c9d246cb36e9627670d5a0940f2da9367aefddcaf13e7eee3477"
    )
    assert domain_sha256_for_test(domain, body + b"\n") == (
        "sha256:b8869a2672c63c8cb426438545381422b308da480da457ce0d5a37f9e9d55545"
    )
    assert domain_sha256_for_test(domain, body) != domain_sha256_for_test(domain, body + b"\n")
    assert json.dumps(identity, sort_keys=True, separators=(",", ":")).encode() == body


def test_target_root_identity_digest_vector_uses_exact_domain_and_cj0_without_lf() -> None:
    identity = {"device": 1, "gid": 20, "inode": 2, "link_count": 5, "mode": 448, "uid": 501}
    body = b'{"device":1,"gid":20,"inode":2,"link_count":5,"mode":448,"uid":501}'
    domain = b"zagrosi-detached-target-root-identity-v1\0"

    assert domain_sha256_for_test(domain, body) == (
        "sha256:659dd7b561a487f0f94dc7bd72565f376b12bc8c96010550abc8ddc64c94401b"
    )
    assert domain_sha256_for_test(domain, body + b"\n") == (
        "sha256:fca3d61843d599c4b8330b6c685e2a6f74a320181008ca97ed6b71229955e6a5"
    )
    assert domain_sha256_for_test(domain, body) != domain_sha256_for_test(domain, body + b"\n")
    assert json.dumps(identity, sort_keys=True, separators=(",", ":")).encode() == body
