# Changelog

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
