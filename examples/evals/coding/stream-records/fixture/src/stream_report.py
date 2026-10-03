"""Existing import report used by the support dashboard."""
from record_stream import decode_records


def summarize(chunks):
    records = decode_records(chunks)
    kinds = {}
    for record in records:
        kind = record.get("kind", "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"records": len(records), "kinds": kinds}
