"""Blind code projections and all-row reviews over retained suite outcomes."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
import random

import coding_trial_assessment as assessment
from coding_trial_assessment_execution import process_passed
from coding_trial_evidence import _text
from coding_trial_inventory import contains_path, copy_snapshot, inventory, project
from coding_trial_qualification import fields, parse_json, read_bytes, require, strings
from coding_trial_study import _absolute, _reference, _save


def _save_ref(context, path, value):
    return assessment._save_ref(context["directory"], path, value)


def _read_ref(context, reference):
    return parse_json(_reference(context["directory"], reference))


def _read_file(context, path):
    raw = read_bytes(context["directory"], path)
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}, parse_json(raw)


def _numbers(path, *, suffix=""):
    if not os.path.lexists(path):
        return []
    _absolute(path, existing=True)
    numbers = []
    for child in path.iterdir():
        name = child.name.removesuffix(suffix) if suffix else child.name
        require(len(name) == 6 and name.isdecimal() and child.name == name + suffix and int(name) > 0,
                "review-stale: Invalid numbered review history")
        _absolute(child, existing=True)
        require(child.is_file() if suffix else child.is_dir(), "review-stale: Invalid review history entry")
        numbers.append(int(name))
    numbers.sort()
    require(numbers == list(range(1, len(numbers) + 1)), "review-stale: Review history is not contiguous")
    return numbers


def _blocks(context):
    count = len(context["suite"]["arms"])
    rng = random.Random(context["suite"]["blind_seed"])
    result = []
    for start in range(0, len(context["items"]), count):
        items = list(context["items"][start:start + count])
        rng.shuffle(items)
        row = items[0]["schedule"]
        result.append((f"B{start // count + 1:06d}", row["task"], row["repeat"], items))
    return result


def _assessed(entries, task):
    scope = task["scope"]
    retained = {name: entry for name, entry in entries.items()
                if not any(contains_path(root, name) for root in scope["generated"])}
    return project(retained, tuple(scope["implementation"] + scope["tests"] + scope["config"]))


def _brief(context, task):
    raw = read_bytes(context["suite_root"], task["brief"])
    if task["clarifications"] is not None:
        raw += b"\n\n" + read_bytes(context["suite_root"], task["clarifications"])
    return raw


def _trial_ref(context, item, reference):
    return {"trial_path": item["trial"].relative_to(context["directory"]).as_posix(), "reference": reference}


def _row(context, label, item):
    return {"label": label, "position": item["schedule"]["position"], "outcome": item["outcome_ref"],
            "behavior": None if item["review"] is None else _trial_ref(context, item, item["review"]["latest"]["behavior"]),
            "not_produced": item["outcome"]["not_produced"]}


def _public_evidence(item):
    review = item["review"]
    value = {"schema": "coding-trial-blind-evidence/v1", "baseline_sha256": None, "candidate_sha256": None,
             "assessment_sha256": None, "not_produced_sha256": None, "checks": None,
             "errors": sorted(set(item["report"]["errors"]))}
    if review is None:
        value["not_produced_sha256"] = item["outcome"]["not_produced"]["sha256"]
        return value
    attempt, behavior, task = review["attempt"], review["behavior"], review["task"]
    native = {row["id"]: row["record"] for row in behavior["native"]}
    statuses = []
    for command in task["checks"]["native"]:
        status = "unknown"
        if command["id"] in native:
            result = assessment._read_ref(item["trial"], native[command["id"]])
            status = "passed" if (process_passed(result["process"]) and result["isolation"]["status"] == "passed"
                                   and result["isolation"]["lifecycle"]["status"] == "verified") else "failed"
        statuses.append({"id": command["id"], "status": status})
    expected = task["checks"]["feature_ids"] + task["checks"]["preservation_ids"]
    oracle = {}
    if behavior["oracle"]["receipt"] is not None:
        try:
            observed = assessment._read_ref(item["trial"], behavior["oracle"]["receipt"])
            checks = observed["checks"]
            if (observed["schema"] == "coding-trial-oracle/v1" and observed["task"] == attempt["identity"]["task"]
                    and observed["candidate_sha256"] == attempt["candidate"]["assessed_sha256"]
                    and type(checks) is list and len(checks) == len(expected)
                    and {check["id"] for check in checks} == set(expected)
                    and all(check["status"] in {"passed", "failed"} for check in checks)):
                oracle = {check["id"]: check["status"] for check in checks}
        except (ValueError, KeyError, TypeError):
            pass  # Malformed oracle output is retained as unknown, never a passing check.
    value.update(baseline_sha256=attempt["identity"]["baseline_sha256"],
                 candidate_sha256=attempt["candidate"]["assessed_sha256"],
                 assessment_sha256=review["latest"]["behavior"]["sha256"], checks={
                     "native": statuses, "oracle": [{"id": name, "status": oracle.get(name, "unknown")} for name in expected],
                     **{key: behavior["checks"][key]["status"] for key in ("scope", "dependencies", "git", "integrity")}})
    return value


def _reviewable(context):
    return all(item["report"]["terminal"] and item["report"]["status"] != "invalid" and (
        item["report"]["produced"] is False or item["report"]["produced"] is True and item["review"] is not None)
        for item in context["items"])


def _latest_packets(context):
    directory = context["directory"]
    blind = _numbers(directory / "blind")
    indexes = _numbers(directory / "review-history/packets", suffix=".json")
    require(blind == indexes, "review-stale: Blind packet generation is incomplete")
    if not indexes:
        return None, None
    generation = indexes[-1]
    reference, index = _read_file(context, f"review-history/packets/{generation:06d}.json")
    fields(index, "schema matrix generation blocks", "Packet index")
    require(index["schema"] == "coding-trial-packets/v1" and index["matrix"] == context["matrix_ref"]
            and type(index["generation"]) is int and index["generation"] == generation,
            "review-stale: Packet source identity differs")
    require(_reviewable(context) and type(index["blocks"]) is list
            and len(index["blocks"]) == len(_blocks(context)), "review-stale: Packet schedule differs")
    for block, (block_id, task_id, repeat, items) in zip(index["blocks"], _blocks(context)):
        fields(block, "id task repeat path inventory rows", "Packet block")
        path = f"blind/{generation:06d}/{block_id}"
        require(block["id"] == block_id and block["task"] == task_id and block["repeat"] == repeat
                and block["path"] == path and block["rows"] == [
                    _row(context, f"C{number:03d}", item) for number, item in enumerate(items, 1)],
                "review-stale: Packet mapping or behavior changed")
        folder = directory / path
        expected = _read_ref(context, block["inventory"])
        require(inventory(folder, excluded=("review.json",)) == expected, "review-stale: Blind packet files changed")
        task = context["suite"]["tasks"][task_id]
        baseline = inventory(context["suite_root"] / task["source"]["export"])
        require(read_bytes(folder, "task.md") == _brief(context, task)
                and inventory(folder / "baseline") == _assessed(baseline, task), "review-stale: Common task packet changed")
        for number, item in enumerate(items, 1):
            label = f"C{number:03d}"
            require(parse_json(read_bytes(folder, f"{label}/evidence.json")) == _public_evidence(item),
                    "review-stale: Public check projection differs")
            candidate = folder / label / "candidate"
            if item["review"] is None:
                require(not os.path.lexists(candidate), "review-stale: No-output label contains candidate files")
            else:
                require(inventory(candidate) == _assessed(item["review"]["candidate"], task),
                        "review-stale: Candidate packet differs")
    return reference, index


def make_packets(context):
    if not _reviewable(context):
        return {"success": False, "packets": [], "errors": [
            {"code": "review-pending", "message": "Every scheduled position needs terminal, reviewable evidence", "path": None}]}
    directory = context["directory"]
    for path in (directory / "blind", directory / "review-history",
                 directory / "review-history/packets", directory / "review-history/packet-inventories"):
        _absolute(path)
    generations = _numbers(directory / "blind")
    require(generations == _numbers(directory / "review-history/packets", suffix=".json"),
            "review-stale: Blind packet generation is incomplete")
    generation = len(generations) + 1
    packet_root = directory / "blind" / f"{generation:06d}"
    inventory_root = directory / "review-history/packet-inventories" / f"{generation:06d}"
    _absolute(packet_root)
    _absolute(inventory_root)
    packet_root.mkdir(parents=True, mode=0o700)
    inventory_root.mkdir(parents=True, mode=0o700)
    index = {"schema": "coding-trial-packets/v1", "matrix": context["matrix_ref"], "generation": generation, "blocks": []}
    for block_id, task_id, repeat, items in _blocks(context):
        folder = packet_root / block_id
        folder.mkdir()
        task = context["suite"]["tasks"][task_id]
        baseline_root = context["suite_root"] / task["source"]["export"]
        baseline = inventory(baseline_root)
        copy_snapshot(baseline_root, folder / "baseline", _assessed(baseline, task))
        with (folder / "task.md").open("xb") as stream:
            stream.write(_brief(context, task))
        form = {"schema": "coding-trial-review/v1", "block": block_id, "reviewer": "", "independent": False,
                "preferred": [], "rationale": "", "candidates": {}}
        rows = []
        for number, item in enumerate(items, 1):
            label = f"C{number:03d}"
            candidate_root = folder / label
            candidate_root.mkdir()
            rows.append(_row(context, label, item))
            _save(candidate_root / "evidence.json", _public_evidence(item))
            if item["review"] is None:
                form["candidates"][label] = {"verdict": "fail", "not_produced_sha256": item["outcome"]["not_produced"]["sha256"]}
            else:
                review = item["review"]
                source = item["trial"] / review["attempt"]["candidate"]["path"]
                copy_snapshot(source, candidate_root / "candidate", _assessed(review["candidate"], task))
                form["candidates"][label] = assessment._review_form(review)["candidates"]["C001"]
        _save(folder / "review.json", form)
        index["blocks"].append({"id": block_id, "task": task_id, "repeat": repeat,
            "path": folder.relative_to(directory).as_posix(), "rows": rows,
            "inventory": _save_ref(context, inventory_root / f"{block_id}.json", inventory(folder, excluded=("review.json",)))})
    parent = directory / "review-history/packets"
    parent.mkdir(exist_ok=True)
    reference = _save_ref(context, parent / f"{generation:06d}.json", index)
    return {"success": True, "generation": generation, "packets": [block["path"] for block in index["blocks"]],
            "index": reference, "errors": []}


def _projection(form, label, task_id):
    return {**{key: form[key] for key in ("schema", "reviewer", "independent", "rationale")},
            "block": task_id, "preferred": ["C001"] if label in form["preferred"] else [],
            "candidates": {"C001": deepcopy(form["candidates"][label])}}


def _forms(context, index):
    result = []
    for block in index["blocks"]:
        items = [context["items"][row["position"]] for row in block["rows"]]
        if not any(item["review"] is not None for item in items):
            continue
        path = block["path"] + "/review.json"
        raw = read_bytes(context["directory"], path)
        form = parse_json(raw)
        fields(form, "schema block reviewer independent preferred rationale candidates", "Comparative review")
        labels = {row["label"] for row in block["rows"]}
        require(form["schema"] == "coding-trial-review/v1" and form["block"] == block["id"]
                and form["independent"] is True and _text(form["reviewer"]) and _text(form["rationale"]),
                "review-stale: An independent comparative review is required")
        strings(form["preferred"], "Preferred candidates", unique=True)
        require(type(form["candidates"]) is dict and set(form["candidates"]) == labels
                and set(form["preferred"]) <= labels, "review-stale: Comparative candidate labels differ")
        projections = []
        for row, item in zip(block["rows"], items):
            label, review = row["label"], item["review"]
            if review is None:
                require(form["candidates"][label] == {"verdict": "fail", "not_produced_sha256": row["not_produced"]["sha256"]}
                        and label not in form["preferred"], "review-stale: Invalid no-output review")
                continue
            projected = _projection(form, label, block["task"])
            passed, _ = assessment._review(projected, review["attempt"], review["latest"]["behavior"],
                                           review["task"], review["baseline"], review["candidate"])
            require(label not in form["preferred"] or item["behavior_ok"], "review-stale: Preferred candidate failed behavior")
            projections.append({"position": row["position"], "form": projected, "passed": passed})
        result.append({"block": block["id"], "path": path, "raw": raw,
                       "sha256": hashlib.sha256(raw).hexdigest(), "projections": projections})
    return result


def _review_outcome(context, item, projected, verdict):
    """Keep expected rejection distinct from new errors during application."""
    errors = list(item["review"]["behavior"]["errors"])
    writer = item["runner"]
    if not process_passed(writer["process"]):
        errors.append(assessment._error("runner-failed", "Writer did not complete successfully"))
    if writer["isolation"]["lifecycle"]["status"] != "verified":
        errors.append(assessment._error("runner-failed", "Writer cleanup was not verified"))
    if not projected["passed"]:
        errors.append(assessment._error("review-stale", "Material review or required cleanup failed"))
    prospective = context["suite"]["purpose"] == "prospective"
    eligible = (prospective and all(gate["status"] == "passed" for gate in item["attempt"]["admission_gates"].values())
                and writer["isolation"]["status"] == "passed")
    quality = "failed" if errors else "passed"
    require(verdict["errors"] == errors and verdict["common_quality"] == quality
            and verdict["synthetic_validation"] == ("not_applicable" if prospective else quality)
            and verdict["study_eligible"] is eligible and verdict["acceptance"] == (quality if eligible else "failed")
            and verdict["cleanup"] == projected["form"]["candidates"]["C001"]["cleanup"],
            "review-stale: Assessment returned an unexpected failure or decision")


def apply_reviews(context):
    reference, index = _latest_packets(context)
    require(index is not None or not any(item["report"]["produced"] is True for item in context["items"]),
            "review-pending: Create complete blind packets before applying reviews")
    if not any(item["report"]["produced"] is True for item in context["items"]):
        return
    forms = _forms(context, index)  # Every row is valid before the first review write.
    parent = context["directory"] / "review-history/applications"
    numbers = _numbers(parent)
    folder = parent / f"{len(numbers) + 1:06d}"
    folder.mkdir(parents=True, mode=0o700)
    for name in ("forms", "projections"):
        (folder / name).mkdir()
    result = {"schema": "coding-trial-review-application/v1", "matrix": context["matrix_ref"],
              "packets": reference, "forms": [], "assessments": [], "status": "completed", "error": None}
    try:
        for form in forms:
            snapshot_path = folder / "forms" / (form["block"] + ".json")
            with snapshot_path.open("xb") as stream:
                stream.write(form["raw"])
            snapshot = {"path": snapshot_path.relative_to(context["directory"]).as_posix(), "sha256": form["sha256"]}
            result["forms"].append({key: form[key] for key in ("block", "path", "sha256")} | {"snapshot": snapshot})
        for form in forms:
            for row in form["projections"]:
                item = context["items"][row["position"]]
                path = folder / "projections" / f"{row['position']:06d}.json"
                _save(path, row["form"])
                verdict = assessment.assess_suite(item["trial"], review=path)
                attempt = parse_json(read_bytes(item["trial"], "trial.json"))
                assessment_ref = attempt["assessments"][-1]
                require(assessment._read_ref(item["trial"], assessment_ref) == verdict,
                        "receipt-invalid: Applied review differs from retained assessment")
                result["assessments"].append({"position": row["position"], "reference": _trial_ref(context, item, assessment_ref)})
                _review_outcome(context, item, row, verdict)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, KeyboardInterrupt) as exc:
        result.update(status="failed", error={"code": "review-stale", "message": str(exc) or "Review application interrupted", "path": None})
    _save(folder / "result.json", result)


def _application(context, reference, index, forms):
    parent = context["directory"] / "review-history/applications"
    numbers = _numbers(parent)
    if not numbers:
        return None
    _, result = _read_file(context, f"review-history/applications/{numbers[-1]:06d}/result.json")
    fields(result, "schema matrix packets forms assessments status error", "Review application")
    require(result["schema"] == "coding-trial-review-application/v1" and result["matrix"] == context["matrix_ref"]
            and result["packets"] == reference and result["status"] == "completed" and result["error"] is None,
            "review-stale: Latest review application is incomplete or stale")
    require(type(result["forms"]) is list and len(result["forms"]) == len(forms), "review-stale: Applied form set differs")
    for saved, form in zip(result["forms"], forms):
        fields(saved, "block path sha256 snapshot", "Applied form")
        require(all(saved[key] == form[key] for key in ("block", "path", "sha256"))
                and _reference(context["directory"], saved["snapshot"]) == form["raw"],
                "review-stale: Original comparative form changed")
    expected = [row for form in forms for row in form["projections"]]
    require(type(result["assessments"]) is list and len(result["assessments"]) == len(expected),
            "review-stale: Applied assessment set differs")
    states = {}
    for saved, projected in zip(result["assessments"], expected):
        fields(saved, "position reference", "Applied assessment")
        position = projected["position"]
        item = context["items"][position]
        fields(saved["reference"], "trial_path reference", "Trial reference")
        require(saved["position"] == position and saved["reference"]["trial_path"] == item["trial"].relative_to(context["directory"]).as_posix(),
                "review-stale: Applied assessment position differs")
        bound = saved["reference"]["reference"]
        attempt = item["attempt"]
        require(bound in attempt["assessments"], "review-stale: Applied assessment is no longer linked")
        verdict = assessment._read_ref(item["trial"], bound)
        require(verdict["schema"] == "coding-trial-assessment/v1" and verdict["kind"] == "review"
                and verdict["identity"] == attempt["identity"]
                and verdict["behavior"] == item["review"]["latest"]["behavior"]
                and assessment._read_ref(item["trial"], verdict["review"]) == projected["form"],
                "review-stale: Projected assessment evidence changed")
        basis = assessment._read_ref(item["trial"], verdict["attempt_basis"])
        history = basis["assessments"]
        require(attempt["assessments"][:len(history) + 1] == [*history, bound], "review-stale: Applied assessment history changed")
        _review_outcome(context, item, projected, verdict)
        states[position] = "passed" if projected["passed"] else "failed"
    return states


def review_status(context):
    produced = [item["schedule"]["position"] for item in context["items"] if item["report"]["produced"] is True]
    if not produced:
        return {}, []
    try:
        reference, index = _latest_packets(context)
        if index is None:
            return {}, []
        numbers = _numbers(context["directory"] / "review-history/applications")
        if not numbers:
            return {}, []
        forms = _forms(context, index)
        return _application(context, reference, index, forms) or {}, []
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return {position: "stale" for position in produced}, [
            {"code": "review-stale", "message": str(exc), "path": None}]
