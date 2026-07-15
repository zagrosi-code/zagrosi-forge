"""Authority boundary for the installed vendored TOML runtime."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path
from typing import Mapping

from .contracts import ForgeError, decode_persistent_record


VENDOR_SCHEMA_DIGEST = (
    "c5e02631a7f8c804180863a9dc88c7aca054c07a62b3ee9dfeb1b08eac13e8c9"
)


def load_vendor_receipt(
    *, candidate_receipt: Path | None = None
) -> Mapping[str, object]:
    """Load only the receipt installed with the trusted running package."""

    if candidate_receipt is not None:
        raise ForgeError(
            "vendor.candidate_authority_rejected",
            10,
            "Candidate vendor metadata cannot override trusted expectations.",
        )
    vendor_root = resources.files("zagrosi_forge._vendor")
    schema = (
        resources.files("zagrosi_forge.install")
        .joinpath("schemas/vendor-receipt-v1.schema.json")
        .read_bytes()
    )
    if hashlib.sha256(schema).hexdigest() != VENDOR_SCHEMA_DIGEST:
        raise ForgeError(
            "vendor.schema_mismatch",
            10,
            "Packaged vendor schema digest does not match.",
        )
    receipt = decode_persistent_record(
        vendor_root.joinpath("vendor-receipt.json").read_bytes()
    )
    if receipt.get("schema_digest") != VENDOR_SCHEMA_DIGEST:
        raise ForgeError(
            "vendor.schema_mismatch", 10, "Vendor receipt schema digest does not match."
        )
    return receipt
