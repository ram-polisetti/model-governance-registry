"""Tamper-evident hash chaining for the registry's audit log.

The pattern follows rai-monitor's incident log: every record carries
``prev_hash`` (the previous record's ``record_hash``, or ``"GENESIS"``
for the first) and ``record_hash`` — the SHA-256 of the record's
canonical JSON *without* ``record_hash`` itself. Any edit, reorder, or
deletion breaks the chain, and :func:`verify_chain` reports it.

What chaining does *not* cover: truncation of the tail is invisible to
the chain alone (the remaining records still link correctly). Operators
who need that guarantee should record the expected event count
externally and compare on verify.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "GENESIS"


def _canonical(record: dict[str, Any]) -> str:
    """Canonical JSON of a record excluding its own ``record_hash``.

    ``seq`` is also excluded: it is a storage artifact assigned by the
    database *after* signing, so it is not part of what was signed.
    """
    body = {k: v for k, v in record.items()
            if k not in ("record_hash", "seq")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def sign_record(record: dict[str, Any], prev_hash: str) -> dict[str, Any]:
    """Return a copy of ``record`` with ``prev_hash``/``record_hash`` set."""
    signed = {**record, "prev_hash": prev_hash}
    signed["record_hash"] = hashlib.sha256(
        _canonical(signed).encode("utf-8")).hexdigest()
    return signed


def verify_chain(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify a hash chain.

    Returns ``{"ok", "records", "signed", "errors"}``. ``ok`` is False
    when any record's hash or ``prev_hash`` linkage fails.
    """
    errors: list[str] = []
    prev_hash = GENESIS
    for i, record in enumerate(records):
        label = f"event {i} (seq={record.get('seq', '?')})"
        stored = record.get("record_hash")
        if not stored:
            errors.append(f"{label}: missing record_hash")
            continue
        if record.get("prev_hash") != prev_hash:
            errors.append(f"{label}: prev_hash mismatch")
        recomputed = hashlib.sha256(
            _canonical(record).encode("utf-8")).hexdigest()
        if recomputed != stored:
            errors.append(f"{label}: record_hash mismatch (tampered)")
        prev_hash = stored
    return {
        "ok": not errors,
        "records": len(records),
        "signed": len(records) - len([e for e in errors
                                      if "missing" in e]),
        "errors": errors,
    }
