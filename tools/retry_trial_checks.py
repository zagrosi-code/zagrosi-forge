"""Independent retry/tenant oracle outside the writer's editable workspace."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys


def verify(workspace: Path) -> int:
    sys.path.insert(0, str(workspace / "src"))
    spec = importlib.util.spec_from_file_location("candidate_dispatch", workspace / "src/dispatch.py")
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    dispatch = candidate.dispatch_batch
    assertions = 0
    # Fixed expectations exercise each failure boundary, duplicate and tenant.
    for size in range(1, 7):
        events = [{"tenant": f"tenant-{i % 2}", "id": str(i // 2), "topic": "created",
                   "payload": {"nested": [i]}} for i in range(size)]
        repeated = [event for event in events for _ in range(2)]
        expected_calls = [(e["tenant"], e["topic"], deepcopy(e["payload"])) for e in events]
        expected_audit = [{k: e[k] for k in ("tenant", "id", "topic")} for e in events]
        for failure in range(size):
            baseline = deepcopy(repeated)
            sent, audit = [], []
            completed = {("untouched", "prior")}
            marker = RuntimeError("transient")

            def send(tenant, topic, payload):
                if len(sent) == failure:
                    raise marker
                sent.append((tenant, topic, deepcopy(payload)))
                payload["nested"].append("mutated by receiver")

            try:
                dispatch(repeated, send, completed, audit)
            except RuntimeError as exc:
                assert exc is marker
            else:
                raise AssertionError("Delivery exception swallowed")
            assert sent == expected_calls[:failure]
            assert audit == expected_audit[:failure]
            assert completed == {("untouched", "prior"), *((e["tenant"], e["id"]) for e in events[:failure])}
            assert repeated == baseline
            assertions += 5

            def recover(tenant, topic, payload):
                sent.append((tenant, topic, deepcopy(payload)))
                payload["nested"].clear()

            assert dispatch(repeated, recover, completed, audit) == {
                "delivered": size - failure, "duplicates": size + failure}
            assert sent == expected_calls
            assert audit == expected_audit
            assert completed == {("untouched", "prior"), *((e["tenant"], e["id"]) for e in events)}
            assert repeated == baseline
            assert dispatch(repeated, recover, completed, audit) == {"delivered": 0, "duplicates": 2 * size}
            assert sent == expected_calls and audit == expected_audit
            assertions += 7
    valid = {"tenant": "t", "id": "1", "topic": "x", "payload": {}}
    invalid = [(None, "Event must be an object"), ({}, "Invalid tenant")]
    invalid.extend(({**valid, key: value}, "Invalid " + key)
                   for key, value in (("tenant", ""), ("id", 0), ("topic", None), ("payload", [])))
    for event, error in invalid:
        sent, completed, audit = [], {("keep", "it")}, [{"keep": "it"}]
        batch = [deepcopy(valid), event]
        before = deepcopy(batch)
        try:
            dispatch(batch, lambda *args: sent.append(args), completed, audit)
        except ValueError as exc:
            assert str(exc) == error
        else:
            raise AssertionError("Invalid batch accepted")
        assert sent == [] and completed == {("keep", "it")} and audit == [{"keep": "it"}]
        assert batch == before
        assertions += 3
    assert dispatch([], lambda *_: None, set(), []) == {"delivered": 0, "duplicates": 0}
    return assertions + 1


if __name__ == "__main__":
    print(json.dumps({"case": sys.argv[2], "assertions": verify(Path(sys.argv[1]))}))
