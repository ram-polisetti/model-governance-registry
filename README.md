# model-governance-registry

A lightweight model governance registry: every model gets a **model card**, a
**NIST AI RMF-style risk assessment**, an **approval workflow** (who approved
it, when, on what evidence), and a **tamper-evident, hash-chained audit trail**
of every change. SQLite-backed, stdlib-only — no third-party dependencies.

The connective tissue between audit tooling
([opsaudit](https://github.com/ram-polisetti/opsaudit),
[rai-monitor](https://github.com/ram-polisetti/rai-monitor)) and governed
systems ([rag-governance-demo](https://github.com/ram-polisetti/rag-governance-demo)):
audit results and monitoring incidents attach to a model record as evidence,
so the approval decision is traceable to the measurements behind it.

## Quickstart

```bash
pip install .
export MGREG_DB=./registry.db   # default: ~/.mgreg/registry.db

mgreg seed                       # 3 fictional example models, or start empty:
mgreg register --name "My Model" --owner "ml-team"
mgreg card --model "My Model" --file examples/card-template.json --by you
mgreg assess --model "My Model" --file examples/risk-template.json \
    --overall-risk medium --by you
mgreg evidence attach-audit --model "My Model" --file audit.json --by you
mgreg submit --model "My Model"
mgreg approve --model "My Model" --approver "risk-lead" \
    --decision approved --rationale "Evidence reviewed; human-in-the-loop enforced."
mgreg show --model "My Model"
mgreg verify                     # verify the audit chain
mgreg sweep --config examples/sweeper-config.json   # stale-approval sweep
mgreg serve --port 8080          # read-mostly web dashboard
```

## Lifecycle

```
draft -> under_review -> approved -> retired
                     -> rejected -> draft -> under_review ...
```

The sweeper can demote `approved -> under_review` when an approval goes
stale, and a waiver can return `under_review -> approved` — every
transition is hash-chained. See [docs/SWEEPER.md](docs/SWEEPER.md).

- Approving requires a model card **and** a risk assessment — a model cannot
  be approved on an empty file, and approvals without a written rationale are
  rejected by the CLI.
- Cards, risk assessments, and approvals are **versioned, never edited**:
  a new version is appended; the old one stays readable.
- Every mutation appends a **hash-chained event** (SHA-256, same pattern as
  rai-monitor's incident log). `mgreg verify` recomputes the chain; any edit,
  reorder, or deletion is reported.

## Evidence integrations

- `mgreg evidence attach-audit --file audit.json` — imports an opsaudit
  `AuditResult` (as saved by `to_dict()`), validates its shape (strongly
  when the `opsaudit` package is importable, structurally otherwise), and
  summarizes the headline fairness numbers (disparate impact, demographic
  parity difference, gate status).
- `mgreg evidence attach-incident --file incident.json` — links a rai-monitor
  incident record, keeping its `id`, `status`, `raised_at`, and `record_hash`
  so the evidence stays traceable to the signed incident log.
- `mgreg evidence attach-note` — free-text notes and document references.

Approval decisions can cite evidence ids, so the record shows *what the
approver looked at*.

## Web dashboard

`mgreg serve` starts a stdlib-only dashboard (no writes — governance actions
stay on the CLI so every change is an explicit, attributed command): a model
index with status/risk badges, and per-model pages showing the card, risk
assessment, approval, evidence, and full audit history. All values are
HTML-escaped.

## Documentation

- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) — governance model and design decisions
- [docs/SWEEPER.md](docs/SWEEPER.md) — the stale-approval sweeper: rules, queue, resolutions
- [docs/SCHEMA.md](docs/SCHEMA.md) — database schema
- [LIMITATIONS.md](LIMITATIONS.md) — what this is not
- [CHANGELOG.md](CHANGELOG.md) — version history

## License

Apache-2.0.
