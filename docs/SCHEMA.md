# Schema

SQLite. All timestamps are UTC `YYYY-MM-DDTHH:MM:SSZ`. JSON columns store
canonical (sorted-key) JSON.

## models

| column     | type | notes                                    |
|------------|------|------------------------------------------|
| id         | TEXT | primary key (16 hex chars)               |
| name       | TEXT | unique, human-readable                   |
| slug       | TEXT | URL-safe derivation of name              |
| owner      | TEXT | team or person accountable               |
| status     | TEXT | draft \| under_review \| approved \| rejected \| retired |
| created_at | TEXT |                                          |

## model_cards / risk_assessments

Versioned artifacts. `(model_id, version)` is unique; nothing is ever
updated in place.

| column       | type | notes                                      |
|--------------|------|--------------------------------------------|
| id           | TEXT | primary key                                |
| model_id     | TEXT | FK → models.id                             |
| version      | INTEGER | 1, 2, 3… per model                      |
| content      | TEXT | JSON. Cards require: purpose, intended_use, training_data, evaluation, limitations. Risks require: govern, map, measure, manage |
| overall_risk | TEXT | risk_assessments only: low \| medium \| high |
| created_at   | TEXT |                                            |
| created_by   | TEXT |                                            |

## approvals

| column       | type | notes                                        |
|--------------|------|----------------------------------------------|
| id           | TEXT | primary key                                  |
| model_id     | TEXT | FK → models.id                               |
| version      | INTEGER | increments per model                      |
| approver     | TEXT | who decided                                  |
| decision     | TEXT | approved \| rejected                         |
| rationale    | TEXT | mandatory written reason                     |
| evidence_ids | TEXT | JSON list of evidence ids cited              |
| created_at   | TEXT |                                              |

## evidence

| column     | type | notes                                                        |
|------------|------|--------------------------------------------------------------|
| id         | TEXT | primary key                                                  |
| model_id   | TEXT | FK → models.id                                               |
| kind       | TEXT | opsaudit_audit \| raimonitor_incident \| document \| note      |
| summary    | TEXT | JSON headline (e.g. DI ratio, incident id/status)             |
| payload    | TEXT | JSON full record (e.g. whole AuditResult dict)                |
| created_at | TEXT |                                                              |
| created_by | TEXT |                                                              |

## audit_events

Append-only, hash-chained. `seq` is assigned by SQLite *after* signing and
is excluded from the signed canonical form.

| column      | type    | notes                                             |
|-------------|---------|---------------------------------------------------|
| seq         | INTEGER | PK autoincrement (not part of the signed record)  |
| prev_hash   | TEXT    | previous record's `record_hash`, or `GENESIS`     |
| record_hash | TEXT    | SHA-256 of the canonical record                   |
| event_type  | TEXT    | model_registered, card_added, risk_added, approval_recorded, evidence_attached, status_changed |
| model_id    | TEXT    | nullable                                          |
| payload     | TEXT    | JSON event detail                                 |
| actor       | TEXT    | who performed the action                          |
| created_at  | TEXT    |                                                   |
