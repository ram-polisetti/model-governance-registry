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
    # approved -> under_review is the sweeper's demotion path: a stale
    # approval no longer holds, so the model goes back under review.
    # under_review -> approved is the waiver path (resolve --decision waive):
    # a human accepts the flagged state as-is, with rationale, and the
    # waiver is recorded in the audit trail. There is no CLI shortcut that
    # approves without an approval record — waivers go through the sweeper.
    # under_review -> retired lets a re-review end in retirement
    # (e.g. the model is superseded while its approval is stale).
    "under_review": ("approved", "rejected", "draft", "retired"),
    "approved": ("under_review", "retired"),
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
        self._migrate()

    # -- migrations ----------------------------------------------------
    def _migrate(self) -> None:
        """Bring older databases up to the current schema.

        New columns on ``approvals`` (approval-time snapshots used by the
        stale-approval sweeper) and the ``re_review_queue`` table are added
        idempotently, so registries created before the sweeper keep working.
        """
        cols = {r["name"] for r in
                self.conn.execute("PRAGMA table_info(approvals)")}
        for col, ddl in (("card_version", "INTEGER"),
                         ("risk_version", "INTEGER"),
                         ("risk_level", "TEXT")):
            if col not in cols:
                self.conn.execute(
                    f"ALTER TABLE approvals ADD COLUMN {col} {ddl}")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS re_review_queue (
            id          TEXT PRIMARY KEY,
            model_id    TEXT NOT NULL REFERENCES models(id),
            rule        TEXT NOT NULL,
            reason      TEXT NOT NULL,
            detail      TEXT NOT NULL DEFAULT '{}',
            detected_at TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'open',
            resolved_at TEXT,
            resolved_by TEXT,
            resolution  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rereview_model
            ON re_review_queue(model_id, status);
        """)
        self.conn.commit()

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
        card = self.latest_card(row["id"])
        risk = self.latest_risk(row["id"])
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
                evidence_ids, created_at,
                card_version, risk_version, risk_level)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, row["id"], version, approver, decision,
             rationale, json.dumps(evidence_ids), self.now(),
             # Snapshots the sweeper compares against: what the approver
             # actually saw. Never updated afterwards — new approvals
             # snapshot anew.
             card["version"], risk["version"], risk["overall_risk"]))
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

    # -- re-review queue (stale-approval sweeper) -------------------------
    def approved_models(self) -> list[dict[str, Any]]:
        """Models whose approval currently holds, with their snapshots."""
        rows = self.conn.execute(
            "SELECT * FROM models WHERE status = 'approved' "
            "ORDER BY created_at")
        out = []
        for r in rows:
            m = dict(r)
            m["approval"] = self.latest_approval(m["id"])
            out.append(m)
        return out

    def flag_for_re_review(self, model_id: str, rule: str, reason: str,
                           detail: dict[str, Any], actor: str
                           ) -> tuple[dict[str, Any], bool]:
        """Open a re-review item and demote the model to under_review.

        Idempotent per (model, rule): if an open item for the same rule
        already exists, nothing happens. Returns ``(item, created)``.
        Every flag and the demotion are hash-chained audit events.
        """
        row = self._model_row(model_id)
        existing = self.conn.execute(
            "SELECT * FROM re_review_queue WHERE model_id = ? AND rule = ?"
            " AND status = 'open'", (row["id"], rule)).fetchone()
        if existing is not None:
            return dict(existing), False
        queue_id = self._new_id()
        self.conn.execute(
            """INSERT INTO re_review_queue
               (id, model_id, rule, reason, detail, detected_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            (queue_id, row["id"], rule, reason,
             json.dumps(detail, sort_keys=True), self.now()))
        self.conn.commit()
        self._event("re_review_flagged", row["id"],
                    {"queue_id": queue_id, "rule": rule,
                     "reason": reason, "detail": detail}, actor)
        # The stale approval no longer holds: back under review — but only
        # when this is a *new* finding. A repeat sweep must not demote a
        # model that a waiver already returned to approved.
        if row["status"] == "approved":
            self.set_status(row["id"], "under_review", actor)
        rec = self.conn.execute(
            "SELECT * FROM re_review_queue WHERE id = ?",
            (queue_id,)).fetchone()
        return dict(rec), True

    def list_re_review(self, status: str | None = None
                       ) -> list[dict[str, Any]]:
        if status not in (None, "open", "resolved", "all"):
            raise RegistryError(f"unknown queue status: {status!r}")
        if status in (None, "open"):
            recs = self.conn.execute(
                "SELECT * FROM re_review_queue WHERE status = 'open'"
                " ORDER BY detected_at")
        elif status == "resolved":
            recs = self.conn.execute(
                "SELECT * FROM re_review_queue WHERE status = 'resolved'"
                " ORDER BY detected_at")
        else:
            recs = self.conn.execute(
                "SELECT * FROM re_review_queue ORDER BY detected_at")
        return [self._queue_row(r) for r in recs]

    def get_queue_item(self, queue_id: str) -> dict[str, Any]:
        rec = self.conn.execute(
            "SELECT * FROM re_review_queue WHERE id = ?", (queue_id,)).fetchone()
        if rec is None:
            raise RegistryError(f"no such re-review item: {queue_id!r}")
        return self._queue_row(rec)

    def latest_waiver(self, model_id: str) -> dict[str, Any] | None:
        """The most recent waive resolution for a model, if any.

        Returns the stored baseline the waiver accepted, so the sweeper
        only re-fires a waived rule when the state changes *again*.
        """
        row = self._model_row(model_id)
        recs = self.conn.execute(
            "SELECT resolution FROM re_review_queue WHERE model_id = ?"
            " AND status = 'resolved' ORDER BY resolved_at DESC",
            (row["id"],))
        for rec in recs:
            try:
                resolution = json.loads(rec["resolution"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if resolution.get("decision") == "waive":
                return resolution.get("baseline") or {}
        return None

    def resolve_re_review(self, queue_id: str, decision: str,
                          rationale: str, actor: str,
                          approver: str | None = None,
                          waiver_baseline: dict[str, Any] | None = None,
                          evidence_ids: list[str] | None = None
                          ) -> dict[str, Any]:
        """Resolve an open re-review item.

        - ``reapprove``: record a fresh approval (needs ``approver``);
          the model returns to ``approved`` with new snapshots. The fresh
          approval covers the whole model, so any *sibling* open items for
          the model are resolved as superseded. ``evidence_ids`` selects
          what the new approval cites (default: all current evidence) —
          cite fresh evidence, not the stale findings, when re-approving.
        - ``retire``: retire the model; sibling open items are superseded.
        - ``waive``: a human accepts the flagged state as-is; only this
          item is resolved and the model returns to ``approved``. The
          waiver baseline is stored so the sweeper only re-fires that rule
          on *further* change.
        """
        if decision not in ("reapprove", "retire", "waive"):
            raise RegistryError(
                "decision must be one of reapprove, retire, waive")
        rationale = rationale.strip()
        if not rationale:
            raise RegistryError("a rationale is required — resolutions "
                                "without reasons are not governance")
        item = self.get_queue_item(queue_id)
        if item["status"] != "open":
            raise RegistryError(f"re-review item {queue_id!r} is already "
                                f"{item['status']}")
        row = self._model_row(item["model_id"])
        if decision == "reapprove":
            if not (approver or "").strip():
                raise RegistryError("reapprove requires an approver")
            if evidence_ids is None:
                evidence_ids = [e["id"] for e in self.list_evidence(row["id"])]
            self.record_approval(row["id"], approver.strip(), "approved",
                                 rationale, list(evidence_ids), actor)
        elif decision == "retire":
            self.set_status(row["id"], "retired", actor)
        else:  # waive
            self._event("approval_waived", row["id"],
                        {"queue_id": queue_id, "rule": item["rule"],
                         "rationale": rationale,
                         "baseline": waiver_baseline or {}}, actor)
            self.set_status(row["id"], "approved", actor)
        resolution = {"decision": decision, "rationale": rationale,
                      "baseline": waiver_baseline or {}}
        self.conn.execute(
            """UPDATE re_review_queue SET status = 'resolved',
               resolved_at = ?, resolved_by = ?, resolution = ?
               WHERE id = ?""",
            (self.now(), actor, json.dumps(resolution, sort_keys=True),
             queue_id))
        self.conn.commit()
        self._event("re_review_resolved", row["id"],
                    {"queue_id": queue_id, "decision": decision,
                     "rationale": rationale}, actor)
        if decision in ("reapprove", "retire"):
            # The fresh decision covers the whole model: sibling open
            # items are superseded, not left dangling.
            for sib in self.list_re_review("open"):
                if sib["model_id"] == row["id"] and sib["id"] != queue_id:
                    self.conn.execute(
                        """UPDATE re_review_queue SET status = 'resolved',
                           resolved_at = ?, resolved_by = ?, resolution = ?
                           WHERE id = ?""",
                        (self.now(), actor,
                         json.dumps({"decision": "superseded",
                                     "rationale": f"superseded by {decision} "
                                                  f"of {queue_id}",
                                     "baseline": {}}, sort_keys=True),
                         sib["id"]))
                    self.conn.commit()
                    self._event("re_review_resolved", row["id"],
                                {"queue_id": sib["id"],
                                 "decision": "superseded",
                                 "rationale": f"superseded by {decision} of "
                                              f"{queue_id}"}, actor)
        return self.get_queue_item(queue_id)

    @staticmethod
    def _queue_row(rec: sqlite3.Row) -> dict[str, Any]:
        out = dict(rec)
        for key in ("detail", "resolution"):
            if isinstance(out.get(key), str):
                try:
                    out[key] = json.loads(out[key])
                except json.JSONDecodeError:
                    pass
        return out

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
