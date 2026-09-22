"""Evidence integrations: import audit results as model evidence.

Two real integrations:

1. **opsaudit** — :func:`import_opsaudit_audit` loads an
   :class:`~opsaudit.metrics.AuditResult` saved via ``to_dict()``
   (a JSON file), validates its shape (strongly when the ``opsaudit``
   package is importable, structurally otherwise), and returns a
   ``(summary, payload)`` pair ready for
   :meth:`mgreg.store.Registry.attach_evidence`. The summary carries the
   headline fairness numbers; the payload keeps the full result.

2. **rai-monitor** — :func:`import_raimonitor_incident` validates a
   single incident record exported from a rai-monitor incident log
   (JSON) and returns the ``(summary, payload)`` pair. The record keeps
   its ``id``, ``status``, ``raised_at`` and ``record_hash`` so the
   registry evidence stays traceable to the signed incident log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Fields every opsaudit AuditResult.to_dict() carries (verified against
# opsaudit's metrics module, 2026-09-22).
OPSAUDIT_REQUIRED = ("groups", "n_total", "demographic_parity_diff",
                     "disparate_impact_ratio")

# Fields every rai-monitor incident record carries (verified against
# rai-monitor's incidents module, 2026-09-22).
RAIMONITOR_REQUIRED = ("id", "status", "raised_at")


def _load_json(path: str | Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}: {exc}") from exc


def import_opsaudit_audit(path: str | Path) -> tuple[dict, dict]:
    """Validate an opsaudit audit-result JSON file and summarize it.

    Returns ``(summary, payload)``. ``summary`` holds the headline
    numbers (disparate impact ratio, demographic parity difference,
    group count, total n, gate status when opsaudit is importable);
    ``payload`` is the full validated result dict.

    Raises :class:`ValueError` when the file is not a valid audit
    result.
    """
    data = _load_json(path)
    if not isinstance(data, dict):
        raise ValueError("opsaudit audit result must be a JSON object")
    missing = [k for k in OPSAUDIT_REQUIRED if k not in data]
    if missing:
        raise ValueError(
            "not an opsaudit AuditResult: missing keys: "
            + ", ".join(missing))

    status = "unknown"
    try:
        from opsaudit.metrics import AuditResult  # type: ignore
        from opsaudit.gate import evaluate_gate_status  # type: ignore
        result = AuditResult.from_dict(data)
        status, _findings = evaluate_gate_status(result)
    except ImportError:
        # Structural validation only — still a real check of the shape,
        # just without opsaudit's own value-level validation.
        if not isinstance(data["groups"], list):
            raise ValueError("opsaudit audit result: 'groups' must be a list")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"opsaudit AuditResult failed validation: {exc}")

    summary = {
        "disparate_impact_ratio": data["disparate_impact_ratio"],
        "demographic_parity_diff": data["demographic_parity_diff"],
        "n_groups": len(data["groups"]),
        "n_total": data["n_total"],
        "flags": data.get("flags", []),
        "gate_status": status,
    }
    return summary, data


def import_raimonitor_incident(path: str | Path) -> tuple[dict, dict]:
    """Validate a rai-monitor incident record JSON file.

    Returns ``(summary, payload)``. ``summary`` holds the incident id,
    status, severity, and raised_at; ``payload`` is the full record,
    including ``record_hash`` so it stays traceable to the signed log.

    Raises :class:`ValueError` when the file is not a valid incident
    record.
    """
    data = _load_json(path)
    if not isinstance(data, dict):
        raise ValueError("rai-monitor incident must be a JSON object")
    missing = [k for k in RAIMONITOR_REQUIRED if k not in data]
    if missing:
        raise ValueError(
            "not a rai-monitor incident record: missing keys: "
            + ", ".join(missing))
    summary = {
        "incident_id": data["id"],
        "status": data["status"],
        "severity": data.get("severity", "unknown"),
        "raised_at": data["raised_at"],
        "record_hash": data.get("record_hash"),
    }
    return summary, data
