#!/usr/bin/env python3
"""Generate the deterministic advisory inventory from the lock and vendor receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any


Identity = tuple[str, str]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LICENSES: dict[Identity, str] = {
    ("colorama", "0.4.6"): "BSD-3-Clause",
    ("hypothesis", "6.156.6"): "MPL-2.0",
    ("iniconfig", "2.3.0"): "MIT",
    ("mypy", "1.20.2"): "MIT",
    ("mypy-extensions", "1.1.0"): "MIT",
    ("packaging", "26.2"): "Apache-2.0 OR BSD-2-Clause",
    ("pathspec", "1.1.1"): "MPL-2.0",
    ("plugin-scanner", "2.0.274"): "Apache-2.0",
    ("pluggy", "1.6.0"): "MIT",
    ("pygments", "2.20.0"): "BSD-2-Clause",
    ("pytest", "9.1.1"): "MIT",
    ("ruff", "0.15.21"): "MIT",
    ("sortedcontainers", "2.4.0"): "Apache-2.0",
    ("typing-extensions", "4.16.0"): "PSF-2.0",
    ("uv-build", "0.11.28"): "Apache-2.0 OR MIT",
}


class InventoryError(ValueError):
    """Raised when the lock cannot produce a complete deterministic inventory."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _artifact_digests(package: dict[str, Any]) -> list[str]:
    artifacts = [package.get("sdist"), *package.get("wheels", [])]
    digests: set[str] = set()
    for artifact in artifacts:
        if not artifact:
            continue
        value = artifact.get("hash", "")
        if not value.startswith("sha256:") or not _SHA256.fullmatch(value[7:]):
            raise InventoryError(
                f"{package['name']} {package['version']} has a non-SHA-256 artifact"
            )
        digests.add(value[7:])
    if package.get("source", {}).get("registry") and not digests:
        raise InventoryError(
            f"{package['name']} {package['version']} has no locked artifact digest"
        )
    return sorted(digests)


def _dependency_identities(
    dependency: dict[str, Any],
    *,
    packages: dict[Identity, dict[str, Any]],
    identities_by_name: dict[str, list[Identity]],
) -> list[Identity]:
    name = dependency["name"]
    version = dependency.get("version")
    if version is not None:
        identity = (name, version)
        if identity not in packages:
            raise InventoryError(f"dependency is absent from lock: {identity!r}")
        return [identity]
    identities = identities_by_name.get(name, [])
    if not identities:
        raise InventoryError(f"dependency is absent from lock: {name!r}")
    return identities


def _advisory_scopes(
    packages: dict[Identity, dict[str, Any]],
    identities_by_name: dict[str, list[Identity]],
    project: dict[str, Any],
) -> dict[Identity, set[str]]:
    scopes: dict[Identity, set[str]] = {identity: set() for identity in packages}
    for group in ("security", "test"):
        stack: list[Identity] = []
        for dependency in project.get("dev-dependencies", {}).get(group, []):
            stack.extend(
                _dependency_identities(
                    dependency,
                    packages=packages,
                    identities_by_name=identities_by_name,
                )
            )
        seen: set[Identity] = set()
        while stack:
            identity = stack.pop()
            if identity in seen:
                continue
            seen.add(identity)
            scopes[identity].add(group)
            package = packages[identity]
            dependencies = list(package.get("dependencies", []))
            for optional in package.get("optional-dependencies", {}).values():
                dependencies.extend(optional)
            for dependency in dependencies:
                stack.extend(
                    _dependency_identities(
                        dependency,
                        packages=packages,
                        identities_by_name=identities_by_name,
                    )
                )
    return scopes


def _scope_name(groups: set[str], identity: Identity) -> str:
    if groups == {"security", "test"}:
        return "security-and-test"
    if groups == {"security"}:
        return "security"
    if groups == {"test"}:
        return "test"
    raise InventoryError(
        f"locked component has no advisory scope: {identity!r} {sorted(groups)!r}"
    )


def generate(lock_path: Path, vendor_receipt_path: Path) -> dict[str, object]:
    lock_bytes = lock_path.read_bytes()
    lock = tomllib.loads(lock_bytes.decode("utf-8"))
    lock_packages: list[dict[str, Any]] = lock["package"]
    project_packages = [
        package
        for package in lock_packages
        if package.get("source", {}).get("editable") == "."
    ]
    if len(project_packages) != 1:
        raise InventoryError("lock must contain exactly one editable root project")
    project = project_packages[0]

    packages: dict[Identity, dict[str, Any]] = {}
    identities_by_name: dict[str, list[Identity]] = {}
    for package in lock_packages:
        identity = (package["name"], package["version"])
        if identity in packages:
            raise InventoryError(f"duplicate locked identity: {identity!r}")
        packages[identity] = package
        identities_by_name.setdefault(identity[0], []).append(identity)
    for identities in identities_by_name.values():
        identities.sort()

    scopes = _advisory_scopes(packages, identities_by_name, project)
    components: list[dict[str, object]] = []
    for identity, package in sorted(packages.items()):
        name, version = identity
        source = package.get("source", {})
        if source.get("editable") == ".":
            components.append(
                {
                    "advisory_scope": "runtime",
                    "kind": "project",
                    "license": "MIT",
                    "name": name,
                    "source_authority": "pyproject.toml",
                    "version": version,
                }
            )
            continue
        registry = source.get("registry")
        if not registry:
            raise InventoryError(
                f"unsupported locked source for {identity!r}: {source!r}"
            )
        components.append(
            {
                "advisory_scope": _scope_name(scopes[identity], identity),
                "artifact_digests": _artifact_digests(package),
                "kind": "locked-python",
                "license": _LICENSES.get(identity, "NOASSERTION"),
                "name": name,
                "source_authority": f"uv.lock;registry={registry}",
                "version": version,
            }
        )

    receipt = json.loads(vendor_receipt_path.read_text(encoding="utf-8"))
    upstream = receipt["upstream"]
    components.append(
        {
            "advisory_scope": "runtime",
            "artifact_digest": upstream["artifact_sha256"],
            "kind": "vendored-python",
            "license": receipt["license"]["expression"],
            "name": upstream["name"],
            "source_authority": (
                f"PyPI sdist and verified tag {upstream['verified_tag_commit']}"
            ),
            "tree_digest": receipt["selected_tree_digest"],
            "version": upstream["version"],
        }
    )
    components.sort(
        key=lambda item: (str(item["name"]), str(item["version"]), str(item["kind"]))
    )
    return {
        "component_count": len(components),
        "components": components,
        "inventory_version": "1.0",
        "lock_digest": hashlib.sha256(lock_bytes).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--vendor-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = generate(args.lock, args.vendor_receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(inventory) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
