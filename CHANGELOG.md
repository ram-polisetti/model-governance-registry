# Changelog

## 0.2.0 — 2026-09-22

Stale-approval sweeper: approvals are a living state, not a checkbox.

- Every approval now snapshots what the approver saw (`card_version`,
  `risk_version`, `risk_level`); older databases are migrated
  idempotently.
- Four config-driven sweep rules: `model_version_changed`,
  `evidence_stale` (missing / past `expires_at` / older than the per-kind
  max age), `risk_reclassified`, `monitor_drift` (read from the disparity
  monitor's state DB, read-only).
- Re-review queue: flags open items with rule + reason + detail, demote
  the model `approved → under_review`; repeats deduplicated.
- Resolutions: `reapprove` (fresh approval with chosen evidence, siblings
  superseded), `retire`, `waive` (risk acceptance with a stored baseline —
  a waived rule only re-fires on further change).
- New lifecycle edges: `approved → under_review` (sweeper demotion),
  `under_review → approved` (waiver), `under_review → retired`.
- CLI: `mgreg sweep`, `mgreg queue`, `mgreg resolve`; dashboard gains a
  `/re-review` page; `examples/sweeper-demo.py` walks one approval going
  stale four ways. 54/54 tests pass (33 existing + 21 new).

## 0.1.0 — 2026-09-22

Initial release.

- Model registry: register, lifecycle (`draft → under_review →
  approved/rejected → retired`), owner tracking.
- Versioned model cards (purpose, intended use, out-of-scope,
  training data, evaluation, limitations) and NIST AI RMF-style risk
  assessments (Govern/Map/Measure/Manage), borrowed from
  rag-governance-demo's formats.
- Approval workflow: approver, decision, mandatory rationale, cited
  evidence ids; approval requires a card and a risk assessment.
- Evidence attachments: opsaudit audit results (validated, summarized)
  and rai-monitor incident records (traceable via record hash), plus
  documents and notes.
- Tamper-evident audit trail: every mutation appends a SHA-256
  hash-chained event; `mgreg verify` recomputes the chain.
- CLI (`mgreg`) and a read-mostly stdlib web dashboard (`mgreg serve`).
- Seeded demo: three fictional example models exercising the full
  lifecycle. 33/33 tests pass.
