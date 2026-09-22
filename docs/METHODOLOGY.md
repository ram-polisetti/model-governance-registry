# Methodology

## Governance model

This registry implements a small, opinionated governance model for ML/AI
systems, aimed at operators rather than compliance departments:

1. **Every model is a record.** Registration captures the name, owner, and
   lifecycle status. Nothing about a model is official until it is in the
   registry.
2. **Describe before you deploy.** A model card (purpose, intended use,
   out-of-scope uses, training data, evaluation, limitations) and a risk
   assessment structured on NIST AI RMF 1.0 (Govern / Map / Measure /
   Manage) are required before approval. The formats are borrowed from
   [rag-governance-demo](https://github.com/ram-polisetti/rag-governance-demo)'s
   `MODEL_CARD.md` and `RISK_ASSESSMENT.md` so the practice is consistent
   across the toolset.
3. **Approvals are decisions with reasons.** An approval records the
   approver, the decision, a written rationale (mandatory — approvals
   without reasons are not governance), and the evidence ids the approver
   relied on.
4. **Evidence is attached, not asserted.** Fairness numbers come from
   imported opsaudit audit results; operational incidents come from linked
   rai-monitor incident records. The registry summarizes the headlines and
   keeps the full payload, so a later reviewer can re-examine the
   underlying measurement.
5. **History is append-only.** Cards, risks, and approvals are versioned,
   never edited. Every mutation appends a hash-chained audit event
   (SHA-256 chain, following rai-monitor's incident-log pattern), and
   `mgreg verify` recomputes the whole chain.

## Design decisions

- **SQLite + stdlib only.** The registry is a single file an operator can
  copy, back up, and inspect with any SQLite client. Zero third-party
  dependencies keeps installation trivial and the supply chain minimal.
- **CLI for writes, web for reads.** Every state change is an explicit,
  attributed command (`--actor` recorded on every event). The dashboard is
  deliberately read-mostly: no forms, no writes, nothing to click by
  accident. (This mirrors the opsaudit pre-deployment gate's philosophy —
  the gate that says no lives in the pipeline, not in a UI.)
- **Evidence ids on approvals.** Citing evidence is optional (a model with
  no measurements can still be honestly approved on documented judgment),
  but when evidence exists the approval points at it, closing the loop
  between measurement and decision.
- **Hash chain, not a ledger.** The audit trail proves *integrity* (nothing
  was altered) rather than *consensus*. Truncation of the tail is invisible
  to the chain alone — operators who need that guarantee should record the
  expected event count externally and compare on verify (same caveat as
  rai-monitor's log).
- **Per-request DB connections in the dashboard.** SQLite connections
  cannot cross threads; the server opens a fresh connection per request
  (found by the test suite, fixed in `server.py`).

## Determinism

Timestamps are injectable (`Registry(path, now=...)`); the test suite runs
on a fixed clock. The hash chain, version numbering, and summaries are
fully deterministic given the same inputs.
