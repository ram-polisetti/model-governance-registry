# Stale-approval sweeper

Registries rot into rubber stamps without enforcement. The sweeper is the
mechanism that keeps an approval a **living state**: on a schedule (cron,
CI, or manual), it re-checks every `approved` model against the conditions
its approval was granted under. When a check fails, the model is demoted to
`under_review` and a **re-review queue** item is opened with the reason
attached. Nothing gets quietly grandfathered.

```bash
mgreg sweep --config examples/sweeper-config.json
mgreg queue                        # list open re-review items
mgreg resolve --id <queue-id> --decision reapprove --approver risk-lead \
    --rationale "v2 card reviewed; scope extension documented."
mgreg resolve --id <queue-id> --decision waive \
    --rationale "RED is a known upstream feed outage, not model behavior."
mgreg resolve --id <queue-id> --decision retire \
    --rationale "Superseded by churn-classifier-v2."
```

## How an approval is remembered

Every approval snapshots **what the approver saw**: the card version, the
risk-assessment version, and the risk tier (`card_version`, `risk_version`,
`risk_level` on the approval record). The sweeper compares the present
against those snapshots — never against a moving target.

Approvals recorded before the sweeper existed have no snapshots. The
sweeper does not grandfather them: it flags them for manual review, since
what was approved cannot be verified.

## The four rules

| Rule | Fires when | Detail carried |
|------|-----------|----------------|
| `model_version_changed` | card or risk version is newer than the approval snapshot | artifact, version at approval → current |
| `evidence_stale` | cited evidence is **missing**, past its `expires_at`, or older than the per-kind max age | evidence id, kind, problem (missing/expired/stale), age vs limit |
| `risk_reclassified` | current risk tier ≠ tier at approval | tier at approval → current |
| `monitor_drift` | the disparity monitor reports `amber`/`red` for the model | monitor status, run id, timestamp |

Rules are individually enableable in the config. The monitor rule reads the
monitor's `model_status` table **read-only** — a missing database, table, or
row is reported in the sweep notes, never treated as a violation.

Evidence staleness, in order: explicit `expires_at` in the evidence summary
or payload wins; otherwise the per-kind max age from the config applies
(`evidence_max_age_days`, with a `default`). Set a kind's limit to a very
large number to effectively disable aging for it.

## What happens on a flag

1. A re-review queue item is opened: rule, human-readable reason, and a
   JSON detail payload. One item per (model, rule); repeats are deduplicated.
2. The model is demoted `approved → under_review` — the stale approval no
   longer holds.
3. Both the flag (`re_review_flagged`) and the demotion (`status_changed`)
   are hash-chained audit events, attributed to the sweeper's actor.

## Resolving

- **reapprove** — a human reviews the flagged state and records a *fresh*
  approval (new snapshots, new rationale). Use `--evidence` to choose what
  the new approval cites; the default cites all current evidence, so attach
  fresh evidence first when the old findings are what went stale. A
  reapproval supersedes any sibling open items for the model.
- **retire** — the model is retired; siblings superseded.
- **waive** — a human accepts the flagged state as-is (risk acceptance).
  The waiver stores a **baseline** of what was accepted. A waived rule only
  fires again when the underlying state changes *further* — e.g. the card
  moves to yet another version, or the monitor flips from amber to red.
  The sweeper never nags about a decision a human already made.

Every resolution requires a rationale and is itself an audit event
(`re_review_resolved`, plus `approval_waived` for waivers).

## Running it on a schedule

The sweeper is a plain CLI command — run it from cron or a scheduled CI
workflow:

```cron
0 6 * * * MGREG_DB=/var/lib/mgreg/registry.db mgreg sweep --config /etc/mgreg/sweeper.json
```

The sweep report (models checked, flags, items created, demotions) is
printed as text or `--json` for log ingestion.

## Demo

`examples/sweeper-demo.py` walks one approval going stale four different
ways — card revision, evidence aging past its limit, risk reclassification,
monitor RED — then resolves each item and shows the waiver holding on
re-sweep. Deterministic; run it with `python3 examples/sweeper-demo.py`.
