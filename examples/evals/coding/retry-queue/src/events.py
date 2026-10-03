"""Validate webhook events before delivering a batch."""


def validate(events):
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        for key in ("tenant", "id", "topic"):
            if not isinstance(event.get(key), str) or not event[key]:
                raise ValueError("Invalid " + key)
        if not isinstance(event.get("payload"), dict):
            raise ValueError("Invalid payload")
