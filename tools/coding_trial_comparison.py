"""Arm-blind code packets and source-bound comparative review; no LOC scoring."""
from __future__ import annotations

import json
from pathlib import Path
import random
import shutil

from coding_trial_evidence import _text, code_fingerprint, files, review_template

CRITERIA = {
    "readability": "Can an engineer follow the main behavior, names, and error paths directly?",
    "cohesion": "Do module/helper boundaries own coherent responsibilities without speculative layers?",
    "duplication": "Are shared causes removed without hiding distinct behavior behind flags or wrappers?",
    "regressions": "Do meaningful tests protect behavior, compatibility, and changed boundaries?",
}


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def packets(directory: Path) -> dict:
    manifest = read(directory / "matrix.json")
    if not manifest.get("comparison"):
        raise ValueError("Blind comparisons require a three-arm matrix")
    destination = directory / "blind"
    blocks = {}
    for item in manifest["trials"]:
        trial = directory / item["id"]
        result_path = trial / "result.json"
        if not result_path.is_file():
            raise ValueError(f"Cannot rank unchecked attempt {item['id']}; preserve the failure and run its check if a workspace exists")
        result = read(result_path)
        if result.get("candidate_sha256") != code_fingerprint(files(trial / "workspace")):
            raise ValueError(f"Recheck changed or unbound candidate before blinding: {item['id']}")
        if not all((trial / "workspace" / name).is_dir() for name in ("src", "tests")):
            raise ValueError(f"Cannot rank missing source/tests for attempt {item['id']}")
        blocks.setdefault(item["block"], []).append(item)
    destination.mkdir(exist_ok=False)
    key = {}
    rng = random.Random(manifest["seed"])
    for block, entries in blocks.items():
        rng.shuffle(entries)
        folder = destination / block
        folder.mkdir()
        reviews = {}
        for index, item in enumerate(entries):
            label = chr(ord("A") + index)
            trial = directory / item["id"]
            result = read(trial / "result.json")
            for name in ("src", "tests"):
                shutil.copytree(trial / "workspace" / name, folder / label / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
            evidence = {"behavior_passed": result["behavior"]["success"],
                        "oracle_complete": result["oracle_complete"],
                        "candidate_tests_passed": result["tests"]["returncode"] == 0,
                        "scope_passed": not result["outside_scope"]}
            (folder / label / "checks.json").write_text(json.dumps(evidence, indent=2) + "\n")
            template = review_template(trial)
            reviews[label] = {"baseline_sha256": template["baseline_sha256"],
                              "candidate_sha256": template["candidate_sha256"],
                              "verdict": "pending", "criteria": {name: "" for name in CRITERIA},
                              "cleanup": template["cleanup"]}
            key[f"{block}/{label}"] = item["id"]
        case = manifest["cases"][entries[0]["case"]]
        baseline = Path(manifest["evaluator_root"]) / "examples/evals/coding" / case.get("fixture", "fixture")
        for name in ("src", "tests"):
            shutil.copytree(baseline / name, folder / "baseline" / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (folder / "review.json").write_text(json.dumps({"reviewer": "", "independent": False,
            "preferred": [], "rationale": "", "candidates": reviews}, indent=2) + "\n")
        (folder / "README.md").write_text(
            "# Blind code comparison\n\nInspect baseline and every candidate's source/tests/checks. "
            "Do not inspect parent directories, logs, timing, or the private arm key. "
            "Judge correctness before preference; ties are allowed. Complete review.json with concrete "
            "file/function evidence for each criterion and the cleanup review. Empty templates grant no pass.\n\n"
            + "\n".join(f"- {name}: {question}" for name, question in CRITERIA.items())
            + "\n\nPrefer useful simplicity and maintainability; line counts alone are not quality evidence. "
            "Arm labels/metrics are withheld, but code may reveal workflow fingerprints; blinding is partial.\n")
    (directory / "blind-key.json").write_text(json.dumps(key, indent=2) + "\n")
    return {"packets": str(destination), "blocks": len(blocks), "private_key": str(directory / "blind-key.json")}


def reviews(directory: Path, *, apply: bool = False) -> list[dict]:
    key_path = directory / "blind-key.json"
    if not key_path.is_file():
        return []
    key = read(key_path)
    results = []
    for path in sorted((directory / "blind").glob("*/review.json")):
        block, data = path.parent.name, read(path)
        candidates = data.get("candidates", {})
        if not isinstance(candidates, dict):
            candidates = {}
        expected = {name.split("/")[-1] for name in key if name.startswith(block + "/")}
        valid = (data.get("independent") is True and _text(data.get("reviewer"))
                 and _text(data.get("rationale")) and set(candidates) == expected
                 and isinstance(data.get("preferred"), list)
                 and all(isinstance(label, str) and label in expected for label in data["preferred"]))
        for label, row in candidates.items():
            trial_id = key.get(f"{block}/{label}")
            if not trial_id or not isinstance(row, dict):
                valid = False
                continue
            trial = directory / trial_id
            template = review_template(trial)
            valid &= (row.get("baseline_sha256") == template["baseline_sha256"]
                      and row.get("baseline_sha256") == code_fingerprint(files(path.parent / "baseline"))
                      and row.get("candidate_sha256") == code_fingerprint(files(trial / "workspace"))
                      and row.get("candidate_sha256") == code_fingerprint(files(path.parent / label))
                      and row.get("verdict") in ("pass", "fixed", "fail")
                      and isinstance(row.get("criteria"), dict)
                      and all(_text(row["criteria"].get(name)) for name in CRITERIA))
            if isinstance(data.get("preferred"), list) and label in data["preferred"]:
                valid &= (row.get("verdict") in ("pass", "fixed")
                          and all(value is True for value in read(path.parent / label / "checks.json").values()))
        if apply and not valid:
            raise ValueError(f"Incomplete or stale independent comparison: {block}")
        if apply:
            for label, row in candidates.items():
                trial = directory / key[f"{block}/{label}"]
                evidence = {name: row.get(name) for name in ("baseline_sha256", "candidate_sha256", "verdict", "cleanup")}
                evidence.update(reviewer=data["reviewer"], independent=True)
                (trial / "review.json").write_text(json.dumps(evidence, indent=2) + "\n")
        results.append({"block": block, "valid": bool(valid), "review": data,
                        "preferred_trials": [key[f"{block}/{label}"] for label in data.get("preferred", []) if label in expected] if valid else []})
    return results
