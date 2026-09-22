"""Tests for the evidence integrations (opsaudit + rai-monitor)."""

import json
import os
import tempfile

import pytest

from mgreg import evidence as evidence_mod
from mgreg.store import Registry

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "src", "mgreg",
                        "seed_data")


@pytest.fixture()
def reg():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    r = Registry(path, now=lambda: "2026-09-22T00:00:00Z")
    yield r
    r.conn.close()
    os.unlink(path)


def test_import_opsaudit_audit():
    summary, payload = evidence_mod.import_opsaudit_audit(
        os.path.join(FIXTURES, "opsaudit-audit-example.json"))
    assert summary["gate_status"] == "pass"
    assert summary["disparate_impact_ratio"] == pytest.approx(0.9375)
    assert summary["n_groups"] == 2
    assert summary["n_total"] == 40
    assert "groups" in payload


def test_import_opsaudit_audit_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(ValueError, match="missing keys"):
        evidence_mod.import_opsaudit_audit(bad)

    notjson = tmp_path / "not.json"
    notjson.write_text("{oops")
    with pytest.raises(ValueError, match="cannot read JSON"):
        evidence_mod.import_opsaudit_audit(notjson)

    notobj = tmp_path / "list.json"
    notobj.write_text("[1, 2]")
    with pytest.raises(ValueError, match="JSON object"):
        evidence_mod.import_opsaudit_audit(notobj)


def test_import_raimonitor_incident():
    summary, payload = evidence_mod.import_raimonitor_incident(
        os.path.join(FIXTURES, "raimonitor-incident-example.json"))
    assert summary["incident_id"] == "INC-EXAMPLE-001"
    assert summary["status"] == "open"
    assert summary["severity"] == "medium"
    assert payload["record_hash"] == "example-not-signed"


def test_import_raimonitor_incident_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "x"}))
    with pytest.raises(ValueError, match="missing keys"):
        evidence_mod.import_raimonitor_incident(bad)


def test_attach_audit_end_to_end(reg):
    m = reg.register("m", "o", "a")
    summary, payload = evidence_mod.import_opsaudit_audit(
        os.path.join(FIXTURES, "opsaudit-audit-example.json"))
    rec = reg.attach_evidence(m["id"], "opsaudit_audit", summary, payload,
                              "author", "a")
    assert rec["kind"] == "opsaudit_audit"
    assert rec["summary"]["gate_status"] == "pass"
    events = reg.history(m["id"])
    assert events[-1]["event_type"] == "evidence_attached"


def test_attach_incident_end_to_end(reg):
    m = reg.register("m", "o", "a")
    summary, payload = evidence_mod.import_raimonitor_incident(
        os.path.join(FIXTURES, "raimonitor-incident-example.json"))
    rec = reg.attach_evidence(m["id"], "raimonitor_incident", summary,
                              payload, "author", "a")
    assert rec["summary"]["incident_id"] == "INC-EXAMPLE-001"
