"""Dead-letter queue backed by a Redis stream.

Stream key: ``osint:dlq``. Entries are flat ``field=value`` maps so they
can be inspected with ``XRANGE`` / ``XREAD`` from ``redis-cli``.
"""
from __future__ import annotations

from typing import Any, Mapping

import structlog

log = structlog.get_logger(__name__)

DLQ_STREAM = "osint:dlq"

# Allow tests to inject a fake redis client.
_client_override: Any | None = None


def set_client(client: Any | None) -> None:
    """Override the redis client (used in tests)."""
    global _client_override
    _client_override = client


async def _get_client() -> Any:
    if _client_override is not None:
        return _client_override
    from app.cache import _client  # reuse app's redis pool

    return await _client()


async def push(entry: Mapping[str, str]) -> str:
    """Append *entry* to the DLQ stream, returns the stream id."""
    r = await _get_client()
    fields = {k: ("" if v is None else str(v)) for k, v in entry.items()}
    sid = await r.xadd(DLQ_STREAM, fields)
    log.info("dlq.push", stream_id=sid, task=fields.get("task"))
    return sid if isinstance(sid, str) else sid.decode("utf-8", "replace")


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:  # noqa: BLE001
            return value.decode("utf-8", "replace")
    return value


def _normalize(item: Any) -> dict[str, Any]:
    sid, fields = item
    sid = _decode(sid)
    out = {}
    if isinstance(fields, dict):
        items = fields.items()
    else:
        items = fields
    for k, v in items:
        out[_decode(k)] = _decode(v)
    return {"id": sid, "fields": out}


async def drain(max: int = 100) -> list[dict[str, Any]]:
    """Read up to *max* entries from the DLQ (non-destructive XRANGE)."""
    if max <= 0:
        return []
    r = await _get_client()
    raw = await r.xrange(DLQ_STREAM, min="-", max="+", count=max)
    return [_normalize(item) for item in raw]


async def requeue(id: str) -> dict[str, Any] | None:
    """Re-emit the DLQ entry with *id* and delete the original.

    Returns the payload that was re-emitted, or ``None`` if not found.
    The caller is responsible for actually re-enqueuing the task on Arq;
    this helper just moves the record to a fresh stream id so external
    tooling can pick it up, and emits a structured log event.
    """
    r = await _get_client()
    raw = await r.xrange(DLQ_STREAM, min=id, max=id, count=1)
    if not raw:
        return None
    entry = _normalize(raw[0])
    fields = dict(entry["fields"])
    fields["requeued_from"] = id
    new_id = await r.xadd(DLQ_STREAM, {k: str(v) for k, v in fields.items()})
    await r.xdel(DLQ_STREAM, id)
    new_id_s = new_id if isinstance(new_id, str) else new_id.decode("utf-8", "replace")
    log.info("dlq.requeue", old_id=id, new_id=new_id_s)
    return {"id": new_id_s, "fields": fields}


async def delete(id: str) -> int:
    """Delete a DLQ entry. Returns the number of deleted entries (0 or 1)."""
    r = await _get_client()
    n = await r.xdel(DLQ_STREAM, id)
    log.info("dlq.delete", id=id, deleted=int(n))
    return int(n)
