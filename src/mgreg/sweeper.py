"""Stale-approval sweeper: approvals are a living state, not a checkbox.

The sweeper scans every ``approved`` model and re-checks the conditions
the approval was granted under. When a rule fires, the model is demoted
to ``under_review`` and a re-review queue item is opened with the reason
attached — nothing gets quietly grandfathered. Every flag, demotion, and
resolution is a hash-chained audit event, so the trail shows exactly when
an approval stopped holding and why.

Rules (all config-driven)::

    model_version_changed  — the card or risk assessment has a newer
                             version than the one the approval snapshotted
    evidence_stale         — cited evidence is missing, past its
                             ``expires_at``, or older than the configured
                             max age for its kind
    risk_reclassified      — the current risk tier differs from the tier
                             the approval snapshotted
    monitor_drift          — the disparity monitor reports amber/red for
                             the model (read from the monitor's state DB)

A waiver (``mgreg resolve --decision waive``) accepts the flagged state
as-is. The waiver stores a baseline; a waived rule only fires again when
the underlying state changes *further* — the sweeper never nags about a
decision a human already made.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import Registry, RegistryError

RULES = ("model_version_changed", "evidence_stale",
         "risk_reclassified", "monitor_drift")

MONITOR_BAD = ("amber", "red")

_TS_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
               "%Y-%m-%dT%H:%M:%S")


def _parse_ts(value: str) -> datetime:
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise RegistryError(f"cannot parse timestamp: {value!r}")


def default_config() -> dict[str, Any]:
    return {
        "actor": "sweeper",
        "rules": {r: True for r in RULES},
        "evidence_max_age_days": {
            "opsaudit_audit": 90,
            "raimonitor_incident": 30,
            "document": 180,
            "note": 365,
            "default": 180,
        },
        "monitors": [],
    }


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a sweeper config file (JSON)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read sweeper config {path}: {exc}")
    if not isinstance(data, dict):
        raise RegistryError("sweeper config top level must be a JSON object")
    cfg = default_config()
    for key in ("actor", "rules", "evidence_max_age_days", "monitors"):
        if key in data:
            if not isinstance(data[key], type(cfg[key])):
                raise RegistryError(
                    f"sweeper config: {key!r} must be a "
                    f"{type(cfg[key]).__name__}")
            cfg[key] = data[key]
    unknown_rules = set(cfg["rules"]) - set(RULES)
    if unknown_rules:
        raise RegistryError(
            "sweeper config: unknown rules: " + ", ".join(sorted(unknown_rules)))
    for rule, enabled in cfg["rules"].items():
        if not isinstance(enabled, bool):
            raise RegistryError(
                f"sweeper config: rule {rule!r} must be true/false, "
                f"got {enabled!r}")
    for entry in cfg["monitors"]:
        if not isinstance(entry, dict) or "model" not in entry \
                or "state_db" not in entry:
            raise RegistryError(
                "sweeper config: each monitor needs 'model' and 'state_db'")
    return cfg


def _now(cfg_now: str | None = None) -> datetime:
    if cfg_now:
        return _parse_ts(cfg_now)
    return datetime.now(timezone.utc)


# -- rule implementations ----------------------------------------------

def _rule_model_version_changed(reg: Registry, model: dict[str, Any],
                                approval: dict[str, Any],
                                baseline: dict[str, Any] | None
                                ) -> list[dict[str, Any]]:
    """Flag when the card/risk moved past what the approval saw."""
    snap_card = approval.get("card_version")
    snap_risk = approval.get("risk_version")
    if baseline:  # a waiver re-baselined what "current" means
        snap_card = baseline.get("card_version", snap_card)
        snap_risk = baseline.get("risk_version", snap_risk)
    if snap_card is None or snap_risk is None:
        # Approvals recorded before snapshots existed: the sweeper cannot
        # verify what was approved, so it refuses to grandfather it.
        return [{"rule": "model_version_changed",
                 "reason": "approval predates version snapshots — "
                           "what was approved cannot be verified",
                 "detail": {"card_version_at_approval": snap_card,
                            "risk_version_at_approval": snap_risk}}]
    flags = []
    card = reg.latest_card(model["id"])
    risk = reg.latest_risk(model["id"])
    if card and card["version"] > snap_card:
        flags.append({"rule": "model_version_changed",
                      "reason": f"model card changed since approval "
                                f"(v{snap_card} -> v{card['version']})",
                      "detail": {"artifact": "card",
                                 "at_approval": snap_card,
                                 "current": card["version"]}})
    if risk and risk["version"] > snap_risk:
        flags.append({"rule": "model_version_changed",
                      "reason": f"risk assessment changed since approval "
                                f"(v{snap_risk} -> v{risk['version']})",
                      "detail": {"artifact": "risk",
                                 "at_approval": snap_risk,
                                 "current": risk["version"]}})
    return flags


def _rule_evidence_stale(reg: Registry, model: dict[str, Any],
                         approval: dict[str, Any], cfg: dict[str, Any],
                         now: datetime,
                         baseline: dict[str, Any] | None
                         ) -> list[dict[str, Any]]:
    """Flag cited evidence that is missing, expired, or too old."""
    max_age = cfg["evidence_max_age_days"]
    waived_evidence = (baseline or {}).get("evidence", {})
    flags = []
    by_id = {e["id"]: e for e in reg.list_evidence(model["id"])}
    for eid in approval.get("evidence_ids") or []:
        ev = by_id.get(eid)
        if ev is None:
            flags.append({"rule": "evidence_stale",
                          "reason": f"cited evidence {eid} is missing "
                                    "from the registry",
                          "detail": {"evidence_id": eid,
                                     "problem": "missing"}})
            continue
        if eid in waived_evidence:
            # Accepted as-is by a human waiver; only a *new* disappearance
            # re-fires (handled above).
            continue
        summary = ev.get("summary") or {}
        payload = ev.get("payload") or {}
        expires_at = summary.get("expires_at") or payload.get("expires_at")
        if expires_at:
            try:
                if _parse_ts(str(expires_at)) < now:
                    flags.append({"rule": "evidence_stale",
                                  "reason": f"evidence {eid} expired "
                                            f"({expires_at})",
                                  "detail": {"evidence_id": eid,
                                             "kind": ev["kind"],
                                             "problem": "expired",
                                             "expires_at": str(expires_at)}})
                    continue
            except RegistryError:
                pass  # unparseable expires_at: fall through to age check
        limit = max_age.get(ev["kind"], max_age.get("default", 180))
        try:
            age_days = (now - _parse_ts(ev["created_at"])).total_seconds() / 86400
        except RegistryError:
            age_days = 0.0
        if age_days > limit:
            flags.append({"rule": "evidence_stale",
                          "reason": f"evidence {eid} ({ev['kind']}) is "
                                    f"{age_days:.0f} days old — older than "
                                    f"the {limit}-day limit",
                          "detail": {"evidence_id": eid, "kind": ev["kind"],
                                     "problem": "stale",
                                     "age_days": round(age_days, 1),
                                     "limit_days": limit}})
    return flags


def _rule_risk_reclassified(reg: Registry, model: dict[str, Any],
                            approval: dict[str, Any],
                            baseline: dict[str, Any] | None
                            ) -> list[dict[str, Any]]:
    """Flag when the risk tier moved since the approval (or waiver)."""
    snap_level = approval.get("risk_level")
    if baseline and "risk_level" in baseline:
        snap_level = baseline["risk_level"]
    risk = reg.latest_risk(model["id"])
    current = risk["overall_risk"] if risk else None
    if snap_level is None or current is None:
        return []
    if current != snap_level:
        return [{"rule": "risk_reclassified",
                 "reason": f"risk tier changed since approval "
                           f"({snap_level} -> {current})",
                 "detail": {"at_approval": snap_level,
                            "current": current}}]
    return []


def _read_monitor_status(state_db: str, monitor_model: str
                         ) -> dict[str, Any] | None:
    """Read the latest monitor status for a model, read-only.

    Returns None when there is no monitor data (missing DB/table/row) —
    the sweeper reports that, but never penalizes a model for it.
    """
    path = Path(state_db)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, run_id, updated_utc FROM model_status "
            "WHERE model = ?", (monitor_model,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return dict(row) if row else None


def _rule_monitor_drift(reg: Registry, model: dict[str, Any],
                        cfg: dict[str, Any],
                        baseline: dict[str, Any] | None
                        ) -> list[dict[str, Any]]:
    """Flag when the disparity monitor reports amber/red."""
    waived = (baseline or {}).get("monitor", {})
    flags = []
    for entry in cfg["monitors"]:
        try:
            target = reg.get_model(entry["model"])
        except RegistryError:
            continue
        if target["id"] != model["id"]:
            continue
        monitor_model = entry.get("monitor_model", entry["model"])
        status = _read_monitor_status(entry["state_db"], monitor_model)
        if status is None:
            continue  # no monitor data: reported, not penalized
        if status["status"] in MONITOR_BAD:
            key = f"{entry['state_db']}::{monitor_model}"
            prev = waived.get(key)
            # A waiver accepted this exact status; only a *change* re-fires.
            if prev is not None and prev.get("status") == status["status"]:
                continue
            flags.append({"rule": "monitor_drift",
                          "reason": f"disparity monitor reports "
                                    f"{status['status'].upper()} for "
                                    f"{monitor_model} "
                                    f"(run {status['run_id']})",
                          "detail": {"monitor_model": monitor_model,
                                     "state_db": entry["state_db"],
                                     "status": status["status"],
                                     "run_id": status["run_id"],
                                     "updated_utc": status["updated_utc"]}})
    return flags


# -- sweep ---------------------------------------------------------------

def sweep(reg: Registry, cfg: dict[str, Any],
          now_iso: str | None = None) -> dict[str, Any]:
    """Run all enabled rules over every approved model.

    Returns a report dict: models checked, flags raised, queue items
    created, models demoted, and per-model notes (e.g. no monitor data).
    """
    now = _now(now_iso)
    actor = cfg.get("actor", "sweeper")
    enabled = cfg["rules"]
    report: dict[str, Any] = {
        "models_checked": 0, "flags": 0, "queue_items_created": 0,
        "models_demoted": 0, "models": [],
    }
    for model in reg.approved_models():
        report["models_checked"] += 1
        approval = model.get("approval")
        entry: dict[str, Any] = {"model": model["name"],
                                 "flags": [], "notes": []}
        if approval is None or approval.get("decision") != "approved":
            entry["notes"].append("no approved approval record; skipped")
            report["models"].append(entry)
            continue
        waiver = reg.latest_waiver(model["id"])
        baseline = (waiver or {})
        flags: list[dict[str, Any]] = []
        if enabled.get("model_version_changed"):
            flags += _rule_model_version_changed(reg, model, approval,
                                                  baseline or None)
        if enabled.get("evidence_stale"):
            flags += _rule_evidence_stale(reg, model, approval, cfg, now,
                                          baseline or None)
        if enabled.get("risk_reclassified"):
            flags += _rule_risk_reclassified(reg, model, approval,
                                             baseline or None)
        if enabled.get("monitor_drift"):
            # note models with configured monitors but no data
            for mon in cfg["monitors"]:
                try:
                    if reg.get_model(mon["model"])["id"] == model["id"]:
                        mm = mon.get("monitor_model", mon["model"])
                        if _read_monitor_status(mon["state_db"], mm) is None:
                            entry["notes"].append(
                                f"no monitor data for {mm} "
                                f"({mon['state_db']})")
                except RegistryError:
                    pass
            flags += _rule_monitor_drift(reg, model, cfg, baseline or None)
        demoted = False
        for flag in flags:
            _, created = reg.flag_for_re_review(
                model["id"], flag["rule"], flag["reason"],
                flag["detail"], actor)
            report["flags"] += 1
            if created:
                report["queue_items_created"] += 1
                demoted = True
        if demoted:
            report["models_demoted"] += 1
        entry["flags"] = flags
        report["models"].append(entry)
    return report


def build_waiver_baseline(reg: Registry, cfg: dict[str, Any],
                          model_id: str) -> dict[str, Any]:
    """Capture what a waiver accepts, so the sweeper only re-fires on
    *further* change."""
    model = reg.get_model(model_id)
    card = reg.latest_card(model["id"])
    risk = reg.latest_risk(model["id"])
    evidence = {e["id"]: {"kind": e["kind"], "created_at": e["created_at"]}
                for e in reg.list_evidence(model["id"])}
    monitor: dict[str, str] = {}
    for entry in cfg.get("monitors", []):
        try:
            target = reg.get_model(entry["model"])
        except RegistryError:
            continue
        if target["id"] != model["id"]:
            continue
        mm = entry.get("monitor_model", entry["model"])
        status = _read_monitor_status(entry["state_db"], mm)
        if status:
            monitor[f"{entry['state_db']}::{mm}"] = {
                "status": status["status"], "run_id": status["run_id"]}
    return {
        "card_version": card["version"] if card else None,
        "risk_version": risk["version"] if risk else None,
        "risk_level": risk["overall_risk"] if risk else None,
        "evidence": evidence,
        "monitor": monitor,
    }
