"""Tests for the registry lifecycle: registration, cards, risks,
approvals, evidence, history, and chain verification."""

import json
import os
import tempfile

import pytest

from mgreg.store import Registry, RegistryError

FIXED_TIME = "2026-09-22T00:00:00Z"

CARD = {
    "purpose": "test purpose",
    "intended_use": "test use",
    "training_data": "test data",
    "evaluation": "test eval",
    "limitations": "test limits",
}

RISK = {
    "govern": "g",
    "map": "m",
    "measure": "me",
    "manage": "ma",
}


@pytest.fixture()
def reg():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    r = Registry(path, now=lambda: FIXED_TIME)
    yield r
    r.conn.close()
    os.unlink(path)


def _full_model(reg, name="m1", owner="o1", actor="a1"):
    m = reg.register(name, owner, actor)
    reg.add_card(m["id"], CARD, "author", actor)
    reg.add_risk(m["id"], RISK, "medium", "assessor", actor)
    return m


def test_register_and_list(reg):
    m = reg.register("Loan Model", "ops", "alice")
    assert m["status"] == "draft"
    assert m["slug"] == "loan-model"
    models = reg.list_models()
    assert [x["name"] for x in models] == ["Loan Model"]


def test_register_duplicate_name(reg):
    reg.register("dup", "ops", "alice")
    with pytest.raises(RegistryError):
        reg.register("dup", "ops", "alice")


def test_register_requires_name_and_owner(reg):
    with pytest.raises(RegistryError):
        reg.register("  ", "ops", "alice")
    with pytest.raises(RegistryError):
        reg.register("x", "  ", "alice")


def test_card_requires_all_sections(reg):
    m = reg.register("m", "o", "a")
    bad = dict(CARD)
    del bad["limitations"]
    with pytest.raises(RegistryError):
        reg.add_card(m["id"], bad, "author", "a")


def test_card_versions(reg):
    m = reg.register("m", "o", "a")
    reg.add_card(m["id"], CARD, "author", "a")
    v2 = dict(CARD, purpose="updated purpose")
    card = reg.add_card(m["id"], v2, "author", "a")
    assert card["version"] == 2
    assert card["content"]["purpose"] == "updated purpose"
    assert len(reg.card_history(m["id"])) == 2


def test_risk_rejects_bad_level(reg):
    m = reg.register("m", "o", "a")
    with pytest.raises(RegistryError):
        reg.add_risk(m["id"], RISK, "extreme", "assessor", "a")


def test_status_transitions(reg):
    m = reg.register("m", "o", "a")
    with pytest.raises(RegistryError):
        reg.set_status(m["id"], "approved", "a")  # draft -> approved illegal
    m = reg.set_status(m["id"], "under_review", "a")
    assert m["status"] == "under_review"
    with pytest.raises(RegistryError):
        reg.set_status(m["id"], "bogus", "a")


def test_approve_requires_card_and_risk(reg):
    m = reg.register("m", "o", "a")
    reg.set_status(m["id"], "under_review", "a")
    with pytest.raises(RegistryError, match="model card"):
        reg.record_approval(m["id"], "boss", "approved", "fine", [], "a")


def test_approve_requires_under_review(reg):
    m = _full_model(reg)
    with pytest.raises(RegistryError, match="under_review"):
        reg.record_approval(m["id"], "boss", "approved", "fine", [], "a")


def test_approve_requires_rationale(reg):
    m = _full_model(reg)
    reg.set_status(m["id"], "under_review", "a")
    with pytest.raises(RegistryError, match="rationale"):
        reg.record_approval(m["id"], "boss", "approved", "  ", [], "a")


def test_approve_happy_path(reg):
    m = _full_model(reg)
    ev = reg.attach_evidence(m["id"], "note", {"title": "t"},
                             {"title": "t", "body": "b"}, "author", "a")
    reg.set_status(m["id"], "under_review", "a")
    approval = reg.record_approval(m["id"], "boss", "approved",
                                   "all checks done", [ev["id"]], "a")
    assert approval["decision"] == "approved"
    assert approval["evidence_ids"] == [ev["id"]]
    assert reg.get_model(m["id"])["status"] == "approved"


def test_approve_rejects_with_bad_evidence_id(reg):
    m = _full_model(reg)
    reg.set_status(m["id"], "under_review", "a")
    with pytest.raises(RegistryError, match="no such evidence"):
        reg.record_approval(m["id"], "boss", "approved", "fine",
                            ["nope"], "a")


def test_double_approval_blocked(reg):
    m = _full_model(reg)
    reg.set_status(m["id"], "under_review", "a")
    reg.record_approval(m["id"], "boss", "approved", "fine", [], "a")
    with pytest.raises(RegistryError, match="under_review"):
        reg.record_approval(m["id"], "boss2", "approved", "again", [], "a")


def test_reject_path(reg):
    m = _full_model(reg)
    reg.set_status(m["id"], "under_review", "a")
    reg.record_approval(m["id"], "boss", "rejected", "not ready", [], "a")
    assert reg.get_model(m["id"])["status"] == "rejected"
    # rejected -> draft -> under_review is the way back
    reg.set_status(m["id"], "draft", "a")
    reg.set_status(m["id"], "under_review", "a")
    assert reg.get_model(m["id"])["status"] == "under_review"


def test_retire(reg):
    m = _full_model(reg)
    reg.set_status(m["id"], "under_review", "a")
    reg.record_approval(m["id"], "boss", "approved", "fine", [], "a")
    reg.set_status(m["id"], "retired", "a")
    assert reg.get_model(m["id"])["status"] == "retired"


def test_evidence_kinds_validated(reg):
    m = reg.register("m", "o", "a")
    with pytest.raises(RegistryError):
        reg.attach_evidence(m["id"], "rumor", {}, {}, "author", "a")
    rec = reg.attach_evidence(m["id"], "document", {"title": "t"},
                              {"title": "t"}, "author", "a")
    assert rec["kind"] == "document"
    assert len(reg.list_evidence(m["id"])) == 1


def test_history_records_every_mutation(reg):
    m = _full_model(reg)
    reg.set_status(m["id"], "under_review", "a")
    events = reg.history(m["id"])
    types = [e["event_type"] for e in events]
    assert types == ["model_registered", "card_added", "risk_added",
                     "status_changed"]


def test_chain_verifies(reg):
    _full_model(reg)
    result = reg.verify()
    assert result["ok"] is True
    assert result["records"] == 3
    assert result["errors"] == []


def test_chain_detects_tampering(reg):
    m = _full_model(reg)
    reg.conn.execute(
        "UPDATE audit_events SET payload = ? WHERE seq = 1",
        (json.dumps({"tampered": True}),))
    reg.conn.commit()
    result = reg.verify()
    assert result["ok"] is False
    assert any("record_hash mismatch" in e for e in result["errors"])


def test_unknown_model(reg):
    with pytest.raises(RegistryError, match="no such model"):
        reg.get_model("ghost")


def test_list_filter_by_status(reg):
    _full_model(reg, name="a")
    _full_model(reg, name="b")
    assert len(reg.list_models("draft")) == 2
    assert reg.list_models("approved") == []
    with pytest.raises(RegistryError):
        reg.list_models("bogus")
