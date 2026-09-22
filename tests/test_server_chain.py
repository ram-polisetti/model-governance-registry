"""Tests for the hash chain and the read-mostly web dashboard."""

import os
import tempfile
import threading
import urllib.request

import pytest

from mgreg import hashchain
from mgreg import seed as seed_mod
from mgreg import server as server_mod
from mgreg.store import Registry


def test_chain_roundtrip():
    records = []
    prev = hashchain.GENESIS
    for i in range(3):
        records.append(hashchain.sign_record({"i": i}, prev))
        prev = records[-1]["record_hash"]
    result = hashchain.verify_chain(records)
    assert result["ok"] is True
    assert result["records"] == 3


def test_chain_detects_reorder():
    records = []
    prev = hashchain.GENESIS
    for i in range(3):
        records.append(hashchain.sign_record({"i": i}, prev))
        prev = records[-1]["record_hash"]
    records[1], records[2] = records[2], records[1]
    assert hashchain.verify_chain(records)["ok"] is False


def test_chain_detects_edit():
    records = [hashchain.sign_record({"i": 0}, hashchain.GENESIS)]
    records[0]["i"] = 99
    result = hashchain.verify_chain(records)
    assert result["ok"] is False
    assert any("tampered" in e for e in result["errors"])


@pytest.fixture()
def seeded_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    reg = Registry(path, now=lambda: "2026-09-22T00:00:00Z")
    seed_mod.seed(reg, actor="test")
    reg.conn.close()
    yield path
    os.unlink(path)


def test_seed_creates_three_models(seeded_db):
    reg = Registry(seeded_db, now=lambda: "2026-09-22T00:00:00Z")
    models = {m["name"]: m for m in reg.list_models()}
    assert set(models) == {"Lending Eligibility Assistant",
                           "Hiring Screen RAG Assistant",
                           "Support Ticket Summarizer"}
    assert models["Lending Eligibility Assistant"]["status"] == "approved"
    assert models["Hiring Screen RAG Assistant"]["status"] == "under_review"
    assert models["Support Ticket Summarizer"]["status"] == "draft"
    assert reg.verify()["ok"] is True
    # lending model has the opsaudit evidence the approval cited
    ev = reg.list_evidence(models["Lending Eligibility Assistant"]["id"])
    kinds = {e["kind"] for e in ev}
    assert "opsaudit_audit" in kinds
    approval = reg.latest_approval(models["Lending Eligibility Assistant"]["id"])
    assert approval["evidence_ids"] == [e["id"] for e in ev
                                        if e["kind"] == "opsaudit_audit"]


def _get(port, path):
    import urllib.error
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                     timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_dashboard_renders(seeded_db):
    server_mod.Handler.db_path = seeded_db
    httpd = server_mod.HTTPServer(("127.0.0.1", 0), server_mod.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, index = _get(port, "/")
        assert status == 200
        assert "Lending Eligibility Assistant" in index
        assert "Hiring Screen RAG Assistant" in index

        reg = Registry(seeded_db)
        model_id = reg.get_model("Lending Eligibility Assistant")["id"]
        status, detail = _get(port, f"/model/{model_id}")
        assert status == 200
        assert "Model card" in detail
        assert "Risk assessment" in detail
        assert "approved" in detail
        assert "Audit trail" in detail

        status, _ = _get(port, "/model/does-not-exist")
        assert status == 404
    finally:
        httpd.shutdown()
        t.join()


def test_dashboard_escapes_html(tmp_path):
    db = str(tmp_path / "x.db")
    reg = Registry(db, now=lambda: "2026-09-22T00:00:00Z")
    evil = "<script>alert(1)</script>"
    reg.register(evil, "owner", "a")
    server_mod.Handler.db_path = db
    httpd = server_mod.HTTPServer(("127.0.0.1", 0), server_mod.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        _, index = _get(port, "/")
        assert "<script>" not in index
        assert "&lt;script&gt;" in index
    finally:
        httpd.shutdown()
        t.join()
