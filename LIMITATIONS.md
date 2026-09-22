# Limitations

What this registry is not, and where its guarantees end.

- **No authentication or authorization.** Anyone with the CLI and the DB
  file can act as any `--actor`. The audit trail records *claimed*
  actors; it does not authenticate them. For a shared deployment, put
  the CLI behind your own identity layer and treat the DB file as
  sensitive.
- **Single-writer assumption.** SQLite serializes writes, so concurrent
  approvals cannot corrupt the database — but the second of two
  simultaneous approvals on the same model will simply fail with
  "must be under_review". There is no locking protocol or conflict
  resolution beyond that.
- **The hash chain proves integrity, not completeness.** Edits, reorders,
  and deletions are detected; truncation of the tail is not (the
  remaining records still link correctly). Record the expected event
  count externally if you need that guarantee.
- **Evidence is referenced, not re-validated.** An attached opsaudit
  result is validated for shape at import time, but the registry does
  not re-run the audit or check that the audited artifact is the one
  being approved. Traceability is a discipline, not a proof.
- **Approvals are single-decider.** There is no multi-approver quorum,
  no delegation, and no expiry of stale approvals. A new card or risk
  version after approval does not invalidate the approval — re-approval
  is a process decision left to the operator.
- **Seed data is fictional.** The three example models, their cards,
  risks, audit numbers, and incidents are synthetic illustrations, all
  labeled as such. Real deployments start from an empty registry
  (skip `mgreg seed`).
- **The dashboard is read-only and local-only.** It binds to 127.0.0.1,
  has no authentication, and performs no writes. Do not expose it to a
  network without a reverse proxy and access control in front of it.
