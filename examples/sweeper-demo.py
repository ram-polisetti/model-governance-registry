"""Sweeper demo: one approval going stale four different ways.

Deterministic end-to-end walkthrough (fixed clocks, scratch databases):

1. Four models are registered, documented, assessed, and approved on
   2025-06-01. Only ``stale-evidence-demo`` cites evidence.
2. On 2026-09-22 the world has moved on:
   - ``stale-card-demo``: the model card was revised (v1 -> v2)
   - ``stale-evidence-demo``: the cited opsaudit audit is now 478 days
     old — past the 90-day limit for ``opsaudit_audit`` evidence
   - ``stale-risk-demo``: the risk tier was reclassified medium -> high
   - ``stale-monitor-demo``: the disparity monitor reports RED
3. The sweeper flags all four, demotes them to ``under_review``, and
   opens re-review queue items with reasons attached.
4. Items are resolved — re-approval where the change is accepted, waiver
   where the flag is a known non-issue — showing the waiver only re-fires
   on *further* change.

Run: ``python3 examples/sweeper-demo.py`` (from the repo root).
Everything is synthetic and clearly labeled as such.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mgreg.store import Registry  # noqa: E402
from mgreg import sweeper as sweeper_mod  # noqa: E402
from mgreg.seed import LENDING_CARD, LENDING_RISK  # noqa: E402

OLD_NOW = "2025-06-01T00:00:00Z"
NEW_NOW = "2026-09-22T00:00:00Z"
ACTOR = "demo"


def _approve(reg: Registry, name: str, risk_level: str,
             cite_evidence: bool = False) -> str:
    m = reg.register(name, "demo-ops", ACTOR)
    reg.add_card(m["id"], dict(LENDING_CARD), "demo-ops", ACTOR)
    reg.add_risk(m["id"], dict(LENDING_RISK), risk_level, "demo-ops", ACTOR)
    evidence_ids: list[str] = []
    if cite_evidence:
        ev = reg.attach_evidence(
            m["id"], "opsaudit_audit",
            {"title": "Q1 disparity audit", "di": 0.94},
            {"title": "Q1 disparity audit", "di": 0.94,
             "detail": "synthetic demo evidence"}, "demo-ops", ACTOR)
        evidence_ids = [ev["id"]]
    reg.set_status(m["id"], "under_review", ACTOR)
    reg.record_approval(m["id"], "demo-compliance-lead", "approved",
                        "Demo approval: card and risk assessment in place.",
                        evidence_ids, ACTOR)
    return m["id"]


def _make_monitor_db(path: Path, model: str, status: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE metric_history (
        model TEXT NOT NULL, metric TEXT NOT NULL, run_id TEXT NOT NULL,
        value REAL, recorded_utc TEXT NOT NULL,
        PRIMARY KEY (model, metric, run_id));
    CREATE TABLE model_status (
        model TEXT PRIMARY KEY, status TEXT NOT NULL,
        run_id TEXT NOT NULL, updated_utc TEXT NOT NULL);
    CREATE TABLE alerts (
        model TEXT NOT NULL, metric TEXT NOT NULL, level TEXT NOT NULL,
        run_id TEXT NOT NULL, sent_utc TEXT NOT NULL,
        runs_at_level INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (model, metric));
    """)
    conn.execute(
        "INSERT INTO model_status (model, status, run_id, updated_utc)"
        " VALUES (?, ?, ?, ?)",
        (model, status, "run-2026-09-21", NEW_NOW))
    conn.commit()
    conn.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sweeper-demo-"))
    db_path = str(tmp / "registry.db")
    monitor_db = tmp / "monitor-state.db"

    # Phase 1: approve everything on 2025-06-01.
    reg = Registry(db_path, now=lambda: OLD_NOW)
    ids = {
        "card": _approve(reg, "stale-card-demo", "medium"),
        "evidence": _approve(reg, "stale-evidence-demo", "medium",
                             cite_evidence=True),
        "risk": _approve(reg, "stale-risk-demo", "medium"),
        "monitor": _approve(reg, "stale-monitor-demo", "medium"),
    }
    _make_monitor_db(monitor_db, "stale-monitor-demo", "red")

    # Phase 2: the world moves on (2026-09-22).
    reg = Registry(db_path, now=lambda: NEW_NOW)
    card_v2 = dict(LENDING_CARD)
    card_v2["limitations"] = card_v2["limitations"] + " v2: scope extended."
    reg.add_card(ids["card"], card_v2, "demo-ops", ACTOR)   # rule 1
    reg.add_risk(ids["risk"], dict(LENDING_RISK), "high",   # rule 3
                 "demo-risk-team", ACTOR)
    # rule 2 needs no new action: the 2025-06-01 evidence is now 478 days
    # old, past the 90-day opsaudit_audit limit. Rule 4 reads the RED
    # monitor state written above.

    cfg_path = tmp / "sweeper-config.json"
    cfg_path.write_text(json.dumps({
        "actor": "demo-sweeper",
        "rules": {r: True for r in sweeper_mod.RULES},
        "evidence_max_age_days": {"opsaudit_audit": 90, "default": 180},
        "monitors": [{"model": "stale-monitor-demo",
                      "monitor_model": "stale-monitor-demo",
                      "state_db": str(monitor_db)}],
    }))
    cfg = sweeper_mod.load_config(cfg_path)

    report = sweeper_mod.sweep(reg, cfg, now_iso=NEW_NOW)
    print(f"checked={report['models_checked']} "
          f"flags={report['flags']} "
          f"queue_items={report['queue_items_created']} "
          f"demoted={report['models_demoted']}")
    by_model: dict[str, list[str]] = {}
    for m in report["models"]:
        for f in m["flags"]:
            by_model.setdefault(m["model"], []).append(f["rule"])
            print(f"  ! {m['model']}: [{f['rule']}] {f['reason']}")
    assert by_model["stale-card-demo"] == ["model_version_changed"], by_model
    assert by_model["stale-evidence-demo"] == ["evidence_stale"], by_model
    assert sorted(by_model["stale-risk-demo"]) == [
        "model_version_changed", "risk_reclassified"], by_model
    assert by_model["stale-monitor-demo"] == ["monitor_drift"], by_model
    assert report["models_demoted"] == 4, report
    assert all(reg.get_model(mid)["status"] == "under_review"
               for mid in ids.values())

    # The audit trail records every flag and demotion.
    events = [e["event_type"] for e in reg.history()]
    assert "re_review_flagged" in events and "status_changed" in events
    assert reg.verify()["ok"]

    # Resolve: re-approve the accepted changes, waive the known non-issue.
    items = reg.list_re_review("open")
    by_model: dict[str, dict[str, dict]] = {}
    for i in items:
        by_model.setdefault(reg.get_model(i["model_id"])["name"], {})[i["rule"]] = i
    card_items = by_model["stale-card-demo"]
    reg.resolve_re_review(card_items["model_version_changed"]["id"], "reapprove",
                          "v2 card reviewed: scope extension is documented "
                          "and within the approved use.",
                          ACTOR, approver="demo-compliance-lead",
                          evidence_ids=[])
    assert reg.get_model(ids["card"])["status"] == "approved"
    # Fresh evidence, then re-approve citing only the fresh audit.
    fresh = reg.attach_evidence(
        ids["evidence"], "opsaudit_audit",
        {"title": "Q3 disparity audit", "di": 0.95},
        {"title": "Q3 disparity audit", "di": 0.95}, "demo-ops", ACTOR)
    ev_items = by_model["stale-evidence-demo"]
    reg.resolve_re_review(ev_items["evidence_stale"]["id"], "reapprove",
                          "Q3 audit attached; approval now rests on it.",
                          ACTOR, approver="demo-compliance-lead",
                          evidence_ids=[fresh["id"]])
    risk_items = by_model["stale-risk-demo"]
    reg.resolve_re_review(risk_items["risk_reclassified"]["id"], "reapprove",
                          "High tier accepted: human-in-the-loop controls "
                          "cover the elevated risk.", ACTOR,
                          approver="demo-compliance-lead", evidence_ids=[])
    mon_items = by_model["stale-monitor-demo"]
    reg.resolve_re_review(
        mon_items["monitor_drift"]["id"], "waive",
        "RED is driven by a known upstream label-feed outage, not model "
        "behavior; re-check at the next monitor run.", ACTOR,
        waiver_baseline=sweeper_mod.build_waiver_baseline(
            reg, cfg, ids["monitor"]))
    assert not reg.list_re_review("open")

    # A second sweep is quiet: fresh snapshots, fresh evidence, and the
    # waiver holds on the unchanged RED.
    report2 = sweeper_mod.sweep(reg, cfg, now_iso=NEW_NOW)
    assert report2["flags"] == 0, report2
    assert report2["models_demoted"] == 0, report2

    # But further change re-fires even a waived rule's *sibling*: the
    # monitor model gets a new card version after the waiver.
    reg.add_card(ids["monitor"], card_v2, "demo-ops", ACTOR)
    report3 = sweeper_mod.sweep(
        Registry(db_path, now=lambda: NEW_NOW), cfg, now_iso=NEW_NOW)
    fired3 = [(m["model"], f["rule"])
              for m in report3["models"] for f in m["flags"]]
    assert ("stale-monitor-demo", "model_version_changed") in fired3, fired3
    # ...while the waived monitor_drift rule stays quiet on the same RED.
    assert ("stale-monitor-demo", "monitor_drift") not in fired3, fired3
    print("demo OK: 4 rules fired, demotions audited, resolutions held, "
          "further change re-fires.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
