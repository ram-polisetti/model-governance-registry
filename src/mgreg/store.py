"""Registry operations: the governed lifecycle of a model record.

Every mutation appends a hash-chained event to ``audit_events`` (see
:mod:`mgreg.hashchain`). Versioned artifacts (cards, risk assessments,
approvals) are never updated in place — a new version is appended and
the old one stays readable, so the full history of a model is always
reconstructible.

Status lifecycle::

    draft -> under_review -> approved -> retired
                         -> rejected

An approval decision of ``approved`` moves a model to ``approved``;
``rejected`` moves it to ``rejected``. Approving requires a model card
and a risk assessment to exist — a model cannot be approved on an empty
file.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any

from . import db
from . import hashchain

STATUSES = ("draft", "under_review", "approved", "rejected", "retired")

TRANSITIONS = {
    "draft": ("under_review", "retired"),
    "under_review": ("approved", "rejected", "draft"),
    "approved": ("retired",),
    "rejected": ("draft",),
    "retired": (),
}

CARD_REQUIRED = ("purpose", "intended_use", "training_data",
                 "evaluation", "limitations")
RISK_REQUIRED = ("govern", "map", "measure", "manage")
RISK_LEVELS = ("low", "medium", "high")
DECISIONS = ("approved", "rejected")
EVIDENCE_KINDS = ("opsaudit_audit", "raimonitor_incident", "document",
                  "note")


class RegistryError(ValueError):
    """Raised when a registry operation violates a governance rule."""


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise RegistryError("model name must contain alphanumeric characters")
    return slug


class Registry:
    def __init__(self, path: str, now: db.NowFn | None = None):
        self.conn = db.connect(path)
        self.now = now or db.utcnow

    # -- low-level -----------------------------------------------------
    def _new_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def _event(self, event_type: str, model_id: str | None,
               payload: dict[str, Any], actor: str) -> None:
        row = self.conn.execute(
            "SELECT record_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev = row["record_hash"] if row else hashchain.GENESIS
        record = hashchain.sign_record({
            "event_type": event_type,
            "model_id": model_id,
            "payload": payload,
            "actor": actor,
            "created_at": self.now(),
        }, prev)
        self.conn.execute(
            """INSERT INTO audit_events
               (prev_hash, record_hash, event_type, model_id, payload,
                actor, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record["prev_hash"], record["record_hash"],
             record["event_type"], record["model_id"],
             json.dumps(record["payload"], sort_keys=True),
             record["actor"], record["created_at"]),
        )
        self.conn.commit()

    def _model_row(self, name_or_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM models WHERE id = ? OR name = ?",
            (name_or_id, name_or_id)).fetchone()
        if row is None:
            raise RegistryError(f"no such model: {name_or_id!r}")
        return row

    # -- models --------------------------------------------------------
    def register(self, name: str, owner: str, actor: str) -> dict[str, Any]:
        name = name.strip()
        owner = owner.strip()
        if not name:
            raise RegistryError("model name is required")
        if not owner:
            raise RegistryError("owner is required")
        model_id = self._new_id()
        try:
            self.conn.execute(
                "INSERT INTO models (id, name, slug, owner, status, created_at)"
                " VALUES (?, ?, ?, ?, 'draft', ?)",
                (model_id, name, _slug(name), owner, self.now()))
            self.conn.commit()
        except sqlite3.IntegrityError:
            raise RegistryError(f"model already registered: {name!r}")
        self._event("model_registered", model_id,
                    {"name": name, "owner": owner}, actor)
        return self.get_model(model_id)

    def get_model(self, name_or_id: str) -> dict[str, Any]:
        return dict(self._model_row(name_or_id))

    def list_models(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None:
            if status not in STATUSES:
                raise RegistryError(f"unknown status: {status!r}")
            rows = self.conn.execute(
                "SELECT * FROM models WHERE status = ? ORDER BY created_at",
                (status,))
        else:
            rows = self.conn.execute("SELECT * FROM models ORDER BY created_at")
        return [dict(r) for r in rows]

    def set_status(self, name_or_id: str, new_status: str,
                   actor: str) -> dict[str, Any]:
        if new_status not in STATUSES:
            raise RegistryError(f"unknown status: {new_status!r}")
        row = self._model_row(name_or_id)
        old = row["status"]
        if new_status not in TRANSITIONS[old]:
            raise RegistryError(
                f"illegal transition {old!r} -> {new_status!r}")
        self.conn.execute("UPDATE models SET status = ? WHERE id = ?",
                          (new_status, row["id"]))
        self.conn.commit()
        self._event("status_changed", row["id"],
                    {"from": old, "to": new_status}, actor)
        return self.get_model(row["id"])

    # -- model cards ---------------------------------------------------
    def _require_keys(self, content: dict[str, Any],
                      required: tuple[str, ...], what: str) -> None:
        missing = [k for k in required
                   if not str(content.get(k, "")).strip()]
        if missing:
            raise RegistryError(
                f"{what} missing required sections: {', '.join(missing)}")

    def add_card(self, name_or_id: str, content: dict[str, Any],
                 created_by: str, actor: str) -> dict[str, Any]:
        self._require_keys(content, CARD_REQUIRED, "model card")
        row = self._model_row(name_or_id)
        version = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM model_cards WHERE model_id = ?",
            (row["id"],)).fetchone()[0] + 1
        card_id = self._new_id()
        self.conn.execute(
            """INSERT INTO model_cards
               (id, model_id, version, content, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (card_id, row["id"], version,
             json.dumps(content, sort_keys=True), self.now(), created_by))
        self.conn.commit()
        self._event("card_added", row["id"],
                    {"card_id": card_id, "version": version}, actor)
        return self.latest_card(row["id"])

    def latest_card(self, name_or_id: str) -> dict[str, Any] | None:
        row = self._model_row(name_or_id)
        rec = self.conn.execute(
            "SELECT * FROM model_cards WHERE model_id = ? "
            "ORDER BY version DESC LIMIT 1", (row["id"],)).fetchone()
        return self._json_row(rec) if rec else None

    def card_history(self, name_or_id: str) -> list[dict[str, Any]]:
        row = self._model_row(name_or_id)
        recs = self.conn.execute(
            "SELECT * FROM model_cards WHERE model_id = ? ORDER BY version",
            (row["id"],))
        return [self._json_row(r) for r in recs]

    # -- risk assessments ----------------------------------------------
    def add_risk(self, name_or_id: str, content: dict[str, Any],
                 overall_risk: str, created_by: str,
                 actor: str) -> dict[str, Any]:
        self._require_keys(content, RISK_REQUIRED, "risk assessment")
        if overall_risk not in RISK_LEVELS:
            raise RegistryError(
                f"overall_risk must be one of {RISK_LEVELS}")
        row = self._model_row(name_or_id)
        version = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM risk_assessments"
            " WHERE model_id = ?", (row["id"],)).fetchone()[0] + 1
        risk_id = self._new_id()
        self.conn.execute(
            """INSERT INTO risk_assessments
               (id, model_id, version, content, overall_risk, created_at,
                created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (risk_id, row["id"], version,
             json.dumps(content, sort_keys=True), overall_risk,
             self.now(), created_by))
        self.conn.commit()
        self._event("risk_added", row["id"],
                    {"risk_id": risk_id, "version": version,
                     "overall_risk": overall_risk}, actor)
        return self.latest_risk(row["id"])

    def latest_risk(self, name_or_id: str) -> dict[str, Any] | None:
        row = self._model_row(name_or_id)
        rec = self.conn.execute(
            "SELECT * FROM risk_assessments WHERE model_id = ? "
            "ORDER BY version DESC LIMIT 1", (row["id"],)).fetchone()
        return self._json_row(rec) if rec else None

    # -- approvals ------------------------------------------------------
    def record_approval(self, name_or_id: str, approver: str,
                        decision: str, rationale: str,
                        evidence_ids: list[str] | None,
                        actor: str) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise RegistryError(f"decision must be one of {DECISIONS}")
        approver = approver.strip()
        rationale = rationale.strip()
        if not approver:
            raise RegistryError("approver is required")
        if not rationale:
            raise RegistryError("a rationale is required — approvals "
                                "without reasons are not governance")
        row = self._model_row(name_or_id)
        if row["status"] != "under_review":
            raise RegistryError(
                "model must be under_review to record an approval; "
                f"current status is {row['status']!r}")
        if self.latest_card(row["id"]) is None:
            raise RegistryError("cannot approve a model with no model card")
        if self.latest_risk(row["id"]) is None:
            raise RegistryError(
                "cannot approve a model with no risk assessment")
        evidence_ids = list(evidence_ids or [])
        for eid in evidence_ids:
            if self.conn.execute(
                    "SELECT 1 FROM evidence WHERE id = ? AND model_id = ?",
                    (eid, row["id"])).fetchone() is None:
                raise RegistryError(f"no such evidence on this model: {eid!r}")
        version = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM approvals WHERE model_id = ?",
            (row["id"],)).fetchone()[0] + 1
        approval_id = self._new_id()
        self.conn.execute(
            """INSERT INTO approvals
               (id, model_id, version, approver, decision, rationale,
                evidence_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, row["id"], version, approver, decision,
             rationale, json.dumps(evidence_ids), self.now()))
        self.conn.commit()
        self._event("approval_recorded", row["id"],
                    {"approval_id": approval_id, "version": version,
                     "decision": decision, "approver": approver}, actor)
        # The approval decision drives the lifecycle.
        self.set_status(row["id"],
                        "approved" if decision == "approved" else "rejected",
                        actor)
        return self.latest_approval(row["id"])

    def latest_approval(self, name_or_id: str) -> dict[str, Any] | None:
        row = self._model_row(name_or_id)
        rec = self.conn.execute(
            "SELECT * FROM approvals WHERE model_id = ? "
            "ORDER BY version DESC LIMIT 1", (row["id"],)).fetchone()
        return self._json_row(rec) if rec else None

    # -- evidence -------------------------------------------------------
    def attach_evidence(self, name_or_id: str, kind: str,
                        summary: dict[str, Any], payload: dict[str, Any],
                        created_by: str, actor: str) -> dict[str, Any]:
        if kind not in EVIDENCE_KINDS:
            raise RegistryError(f"kind must be one of {EVIDENCE_KINDS}")
        row = self._model_row(name_or_id)
        evidence_id = self._new_id()
        self.conn.execute(
            """INSERT INTO evidence
               (id, model_id, kind, summary, payload, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (evidence_id, row["id"], kind,
             json.dumps(summary, sort_keys=True),
             json.dumps(payload, sort_keys=True), self.now(), created_by))
        self.conn.commit()
        self._event("evidence_attached", row["id"],
                    {"evidence_id": evidence_id, "kind": kind}, actor)
        rec = self.conn.execute("SELECT * FROM evidence WHERE id = ?",
                                (evidence_id,)).fetchone()
        return self._json_row(rec)

    def list_evidence(self, name_or_id: str) -> list[dict[str, Any]]:
        row = self._model_row(name_or_id)
        recs = self.conn.execute(
            "SELECT * FROM evidence WHERE model_id = ? ORDER BY created_at",
            (row["id"],))
        return [self._json_row(r) for r in recs]

    # -- history & verification -----------------------------------------
    def history(self, name_or_id: str | None = None) -> list[dict[str, Any]]:
        if name_or_id is None:
            recs = self.conn.execute(
                "SELECT * FROM audit_events ORDER BY seq")
        else:
            row = self._model_row(name_or_id)
            recs = self.conn.execute(
                "SELECT * FROM audit_events WHERE model_id = ? ORDER BY seq",
                (row["id"],))
        return [self._event_row(r) for r in recs]

    def verify(self) -> dict[str, Any]:
        return hashchain.verify_chain(self.history())

    # -- row helpers -----------------------------------------------------
    @staticmethod
    def _json_row(rec: sqlite3.Row) -> dict[str, Any]:
        out = dict(rec)
        for key in ("content", "summary", "payload", "evidence_ids"):
            if key in out and isinstance(out[key], str):
                out[key] = json.loads(out[key])
        return out

    @staticmethod
    def _event_row(rec: sqlite3.Row) -> dict[str, Any]:
        out = dict(rec)
        out["payload"] = json.loads(out["payload"])
        return out
