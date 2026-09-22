"""Tests for the stale-approval sweeper: rules, queue, demotion, resolve."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mgreg.store import Registry, RegistryError
from mgreg import sweeper as sweeper_mod
from mgreg.seed import LENDING_CARD, LENDING_RISK

NOW = "2026-09-22T00:00:00Z"
OLD_NOW = "2025-01-01T00:00:00Z"
ACTOR = "tester"


def make_reg(path: Path, now: str = NOW) -> Registry:
    return Registry(str(path), now=lambda: now)


def approve_model(reg: Registry, name: str = "churn-classifier",
                  risk_level: str = "medium") -> str:
    m = reg.register(name, "demo-ops", ACTOR)
    reg.add_card(m["id"], dict(LENDING_CARD), "demo-ops", ACTOR)
    reg.add_risk(m["id"], dict(LENDING_RISK), risk_level, "demo-ops", ACTOR)
    ev = reg.attach_evidence(
        m["id"], "opsaudit_audit", {"title": "audit"}, {"title": "audit"},
        "demo-ops", ACTOR)
    reg.set_status(m["id"], "under_review", ACTOR)
    reg.record_approval(m["id"], "demo-lead", "approved",
                        "test approval", [ev["id"]], ACTOR)
    return m["id"]


def base_cfg(**overrides):
    cfg = sweeper_mod.default_config()
    cfg.update(overrides)
    return cfg


def test_sweep_clean_when_nothing_changed(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    approve_model(reg)
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    assert report == {"models_checked": 1, "flags": 0,
                      "queue_items_created": 0, "models_demoted": 0,
                      "models": report["models"]}
    assert reg.list_re_review("open") == []


def test_rule1_fires_on_card_change(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    assert report["flags"] == 1
    assert report["models_demoted"] == 1
    item = reg.list_re_review("open")[0]
    assert item["rule"] == "model_version_changed"
    assert "v1 -> v2" in item["reason"]
    assert reg.get_model(mid)["status"] == "under_review"


def test_rule1_fires_on_risk_change_same_tier(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    reg.add_risk(mid, dict(LENDING_RISK), "medium", "demo-ops", ACTOR)
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    rules = [f["rule"] for m in report["models"] for f in m["flags"]]
    assert rules == ["model_version_changed"]  # risk_reclassified stays quiet


def test_rule1_legacy_approval_without_snapshot(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    # Simulate a pre-sweeper approval: wipe the snapshots.
    reg.conn.execute(
        "UPDATE approvals SET card_version = NULL, risk_version = NULL,"
        " risk_level = NULL WHERE model_id = ?", (mid,))
    reg.conn.commit()
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    flags = [f for m in report["models"] for f in m["flags"]]
    assert len(flags) == 1
    assert "predates version snapshots" in flags[0]["reason"]
    # Not grandfathered: still demoted.
    assert reg.get_model(mid)["status"] == "under_review"


def test_rule2_missing_evidence(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    eid = reg.latest_approval(mid)["evidence_ids"][0]
    reg.conn.execute("DELETE FROM evidence WHERE id = ?", (eid,))
    reg.conn.commit()
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    flags = [f for m in report["models"] for f in m["flags"]]
    assert len(flags) == 1
    assert flags[0]["rule"] == "evidence_stale"
    assert flags[0]["detail"]["problem"] == "missing"


def test_rule2_expired_evidence(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    eid = reg.latest_approval(mid)["evidence_ids"][0]
    reg.conn.execute(
        "UPDATE evidence SET summary = ? WHERE id = ?",
        (json.dumps({"title": "audit", "expires_at": "2026-01-01T00:00:00Z"}),
         eid))
    reg.conn.commit()
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    flags = [f for m in report["models"] for f in m["flags"]]
    assert len(flags) == 1
    assert flags[0]["detail"]["problem"] == "expired"


def test_rule2_stale_by_age(tmp_path):
    reg = make_reg(tmp_path / "old.db", now=OLD_NOW)
    mid = approve_model(reg)  # evidence created 2025-01-01
    reg = make_reg(tmp_path / "old.db", now=NOW)
    cfg = base_cfg()
    cfg["evidence_max_age_days"] = {"opsaudit_audit": 90, "default": 180}
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    flags = [f for m in report["models"] for f in m["flags"]]
    assert len(flags) == 1
    assert flags[0]["detail"]["problem"] == "stale"
    assert flags[0]["detail"]["age_days"] > 90


def test_rule2_fresh_evidence_passes(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    approve_model(reg)
    cfg = base_cfg()
    cfg["evidence_max_age_days"] = {"opsaudit_audit": 90, "default": 180}
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    assert report["flags"] == 0


def test_rule3_risk_reclassified(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg, risk_level="medium")
    reg.add_risk(mid, dict(LENDING_RISK), "high", "demo-ops", ACTOR)
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    rules = sorted(f["rule"] for m in report["models"] for f in m["flags"])
    assert rules == ["model_version_changed", "risk_reclassified"]
    rr = [f for m in report["models"] for f in m["flags"]
          if f["rule"] == "risk_reclassified"][0]
    assert "medium -> high" in rr["reason"]


def _monitor_db(path: Path, model: str, status: str) -> str:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE model_status (model TEXT PRIMARY KEY,"
                 " status TEXT NOT NULL, run_id TEXT NOT NULL,"
                 " updated_utc TEXT NOT NULL)")
    conn.execute("INSERT INTO model_status VALUES (?, ?, ?, ?)",
                 (model, status, "run-1", NOW))
    conn.commit()
    conn.close()
    return str(path)


def test_rule4_monitor_red(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    approve_model(reg, name="churn-classifier")
    mdb = _monitor_db(tmp_path / "mon.db", "churn-classifier", "red")
    cfg = base_cfg(monitors=[{"model": "churn-classifier",
                              "state_db": mdb}])
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    flags = [f for m in report["models"] for f in m["flags"]]
    assert len(flags) == 1
    assert flags[0]["rule"] == "monitor_drift"
    assert "RED" in flags[0]["reason"]


def test_rule4_monitor_green_passes(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    approve_model(reg, name="churn-classifier")
    mdb = _monitor_db(tmp_path / "mon.db", "churn-classifier", "green")
    cfg = base_cfg(monitors=[{"model": "churn-classifier", "state_db": mdb}])
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    assert report["flags"] == 0


def test_rule4_missing_monitor_is_noted_not_flagged(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    approve_model(reg, name="churn-classifier")
    cfg = base_cfg(monitors=[{"model": "churn-classifier",
                              "state_db": str(tmp_path / "nope.db")}])
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    assert report["flags"] == 0
    assert any("no monitor data" in n for m in report["models"]
               for n in m["notes"])


def test_sweep_audits_everything(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    events = [e["event_type"] for e in reg.history(mid)]
    assert "re_review_flagged" in events
    assert "status_changed" in events
    assert reg.verify()["ok"]
    assert reg.verify()["records"] == len(events)


def test_flag_idempotent_per_rule(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    item1, created1 = reg.flag_for_re_review(
        mid, "model_version_changed", "r", {"a": 1}, ACTOR)
    item2, created2 = reg.flag_for_re_review(
        mid, "model_version_changed", "r", {"a": 1}, ACTOR)
    assert created1 is True and created2 is False
    assert item1["id"] == item2["id"]
    assert len(reg.list_re_review("open")) == 1


def test_resolve_reapprove(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    item = reg.list_re_review("open")[0]
    reg.resolve_re_review(item["id"], "reapprove",
                          "v2 reviewed and accepted", ACTOR,
                          approver="demo-lead-2")
    assert reg.get_model(mid)["status"] == "approved"
    assert reg.latest_approval(mid)["card_version"] == 2
    assert reg.get_queue_item(item["id"])["status"] == "resolved"
    # Fresh snapshots: a re-sweep is quiet.
    report = sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    assert report["flags"] == 0


def test_resolve_retire(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    item = reg.list_re_review("open")[0]
    reg.resolve_re_review(item["id"], "retire",
                          "superseded by v2 model", ACTOR)
    assert reg.get_model(mid)["status"] == "retired"


def test_resolve_waive_holds_until_further_change(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg, name="churn-classifier")
    mdb = _monitor_db(tmp_path / "mon.db", "churn-classifier", "red")
    cfg = base_cfg(monitors=[{"model": "churn-classifier", "state_db": mdb}])
    sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    item = reg.list_re_review("open")[0]
    baseline = sweeper_mod.build_waiver_baseline(reg, cfg, mid)
    reg.resolve_re_review(item["id"], "waive",
                          "RED is a known upstream feed outage", ACTOR,
                          waiver_baseline=baseline)
    assert reg.get_model(mid)["status"] == "approved"
    # Same RED does not re-fire.
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    assert report["flags"] == 0
    # Further change re-fires.
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    report = sweeper_mod.sweep(reg, cfg, now_iso=NOW)
    rules = [f["rule"] for m in report["models"] for f in m["flags"]]
    assert "model_version_changed" in rules
    assert "monitor_drift" not in rules  # waiver still holds for the RED


def test_resolve_validation(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    item = reg.list_re_review("open")[0]
    with pytest.raises(RegistryError):
        reg.resolve_re_review(item["id"], "reapprove", "", ACTOR,
                              approver="x")
    with pytest.raises(RegistryError):
        reg.resolve_re_review(item["id"], "reapprove", "ok", ACTOR)
    with pytest.raises(RegistryError):
        reg.resolve_re_review(item["id"], "bogus", "ok", ACTOR)
    with pytest.raises(RegistryError):
        reg.resolve_re_review("nope", "retire", "ok", ACTOR)
    reg.resolve_re_review(item["id"], "retire", "done", ACTOR)
    with pytest.raises(RegistryError):
        reg.resolve_re_review(item["id"], "retire", "again", ACTOR)


def test_config_validation(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(RegistryError):
        sweeper_mod.load_config(bad)
    bad.write_text(json.dumps({"rules": {"nope": True}}))
    with pytest.raises(RegistryError):
        sweeper_mod.load_config(bad)
    bad.write_text(json.dumps({"monitors": [{"model": "x"}]}))
    with pytest.raises(RegistryError):
        sweeper_mod.load_config(bad)
    bad.write_text(json.dumps({"rules": {"evidence_stale": "yes"}}))
    with pytest.raises(RegistryError):
        sweeper_mod.load_config(bad)


def test_migration_adds_snapshot_columns(tmp_path):
    path = tmp_path / "r.db"
    reg = make_reg(path)
    # Simulate a pre-sweeper database.
    reg.conn.execute("ALTER TABLE approvals DROP COLUMN card_version")
    reg.conn.execute("ALTER TABLE approvals DROP COLUMN risk_version")
    reg.conn.execute("ALTER TABLE approvals DROP COLUMN risk_level")
    reg.conn.execute("DROP TABLE re_review_queue")
    reg.conn.commit()
    reg2 = Registry(str(path), now=lambda: NOW)  # re-migrates
    cols = {r["name"] for r in
            reg2.conn.execute("PRAGMA table_info(approvals)")}
    assert {"card_version", "risk_version", "risk_level"} <= cols
    mid = approve_model(reg2)
    assert reg2.latest_approval(mid)["card_version"] == 1
    assert reg2.latest_approval(mid)["risk_level"] == "medium"


def test_queue_list_filters(tmp_path):
    reg = make_reg(tmp_path / "r.db")
    mid = approve_model(reg)
    card = dict(LENDING_CARD)
    card["limitations"] += " v2"
    reg.add_card(mid, card, "demo-ops", ACTOR)
    sweeper_mod.sweep(reg, base_cfg(), now_iso=NOW)
    assert len(reg.list_re_review("open")) == 1
    assert len(reg.list_re_review("resolved")) == 0
    item = reg.list_re_review("open")[0]
    reg.resolve_re_review(item["id"], "retire", "done", ACTOR)
    assert len(reg.list_re_review("open")) == 0
    assert len(reg.list_re_review("resolved")) == 1
    assert len(reg.list_re_review("all")) == 1
    with pytest.raises(RegistryError):
        reg.list_re_review("bogus")
