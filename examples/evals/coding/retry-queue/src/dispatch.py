"""Ordered webhook delivery with a caller-owned completion journal."""
from copy import deepcopy
from events import validate


def dispatch_batch(events, send, completed, audit):
    """Return delivered/duplicate counts; preserve successful progress on failure.

    send(tenant, topic, payload) may raise. The caller retries the original batch
    using the same completed set and audit list. This synchronous API does not
    promise exactly-once delivery after process crashes or concurrent calls.
    """
    validate(events)
    delivered = duplicates = 0
    for event in events:
        key = event["id"]
        if key in completed:
            duplicates += 1
            continue
        completed.add(key)
        send(event["tenant"], event["topic"], deepcopy(event["payload"]))
        audit.append({"tenant": event["tenant"], "id": event["id"], "topic": event["topic"]})
        delivered += 1
    return {"delivered": delivered, "duplicates": duplicates}
