"""Seed data: three fictional example models so the registry and the
dashboard are not empty on first run.

Everything here is synthetic and illustrative — small, hand-written
examples that exercise every registry path (card, risk, evidence,
approval, rejection, draft). Real deployments start from an empty
registry.
"""

from __future__ import annotations

from . import evidence as evidence_mod
from .store import Registry


def _res(path: str) -> str:
    from pathlib import Path
    return str(Path(__file__).parent / "seed_data" / path)


LENDING_CARD = {
    "purpose": "Decision-support assistant that drafts eligibility summaries "
               "for small-business loan applications from the approved lending "
               "policy corpus.",
    "intended_use": "Internal use by loan officers. Every draft cites the "
                    "policy chunk it came from; a human officer makes the "
                    "final decision.",
    "out_of_scope": "Fully automated approvals. Consumer lending. Any use "
                    "outside the approved lending policy corpus.",
    "training_data": "No model training. Retrieval over a synthetic, "
                     "fictional lending policy corpus (example data only).",
    "evaluation": "37-case eval harness pattern: groundedness, citation "
                  "correctness, refusal on out-of-corpus queries. "
                  "Disparate-impact audit on the eligibility classifier: "
                  "DI=0.9375 (see attached opsaudit evidence).",
    "limitations": "Extractive drafts only; cannot explain policy beyond "
                   "the corpus. Fairness holds only for the audited "
                   "population mix — re-audit when the applicant mix shifts.",
}

LENDING_RISK = {
    "govern": "Owner: example-loan-ops team. Policy: no automated decisions; "
              "human officer signs every outcome. Audit log reviewed weekly.",
    "map": "Stakeholders: applicants, loan officers, compliance. Failure "
           "modes: stale policy answered as current; over-reliance on "
           "drafts; demographic disparity in the underlying classifier.",
    "measure": "Eval harness re-run on every corpus change; opsaudit "
               "disparity audit quarterly (thresholds: DI >= 0.80, "
               "|DP diff| <= 0.10).",
    "manage": "Refusal path is the kill switch. Incident response: trace "
              "via citation to chunk to source doc; fix doc; re-run evals.",
}

HIRING_CARD = {
    "purpose": "RAG assistant that answers recruiter questions from the "
               "approved hiring policy corpus (interview rubrics, "
               "compensation bands).",
    "intended_use": "Recruiters preparing interviews. Never scores or ranks "
                    "candidates.",
    "out_of_scope": "Candidate screening, ranking, or automated rejection. "
                    "Anything outside the hiring policy corpus.",
    "training_data": "Synthetic, fictional hiring policy corpus (example "
                     "data only). No candidate data.",
    "evaluation": "Groundedness and citation evals pass; red-team refusal "
                  "10/10. No disparity audit yet — candidate-facing risk "
                  "is indirect but the domain is sensitive.",
    "limitations": "Cannot verify policy freshness; compensation bands go "
                   "stale. Advisory only.",
}

HIRING_RISK = {
    "govern": "Owner: example-people-ops. Policy: advisory use only; "
              "candidate decisions stay fully human.",
    "map": "Stakeholders: recruiters, candidates (indirect), HR compliance. "
           "Failure modes: stale compensation bands quoted as current; "
           "prompt injection via a poisoned policy doc; drift in refusal "
           "behavior after corpus updates.",
    "measure": "Eval harness on every corpus change; drift monitoring on "
               "refusal/escalation rates (example rai-monitor incident "
               "attached: refusal-rate dip, under investigation).",
    "manage": "Weekly audit-log review; corpus changes require re-eval "
              "before deploy.",
}

SUPPORT_CARD = {
    "purpose": "Draft summaries of customer support tickets for agents.",
    "intended_use": "Internal agent productivity. Agent reviews every draft "
                    "before sending.",
    "out_of_scope": "Customer-facing auto-replies. Ticket prioritization.",
    "training_data": "No training; summarization over ticket text at "
                     "request time. No data retained.",
    "evaluation": "Not yet evaluated — draft stage.",
    "limitations": "May omit critical ticket details; agent review is "
                   "mandatory.",
}


def seed(reg: Registry, actor: str = "seed") -> list[str]:
    """Seed the demo registry. Returns the model names created."""
    created = []

    # 1. Lending Eligibility Assistant — the full lifecycle: card, risk,
    #    opsaudit evidence, approval.
    m = reg.register("Lending Eligibility Assistant", "example-loan-ops",
                     actor)
    reg.add_card(m["id"], LENDING_CARD, "example-loan-ops", actor)
    reg.add_risk(m["id"], LENDING_RISK, "medium", "example-risk-team",
                 actor)
    summary, payload = evidence_mod.import_opsaudit_audit(
        _res("opsaudit-audit-example.json"))
    ev = reg.attach_evidence(m["id"], "opsaudit_audit", summary, payload,
                             "example-risk-team", actor)
    reg.set_status(m["id"], "under_review", actor)
    reg.record_approval(m["id"], "example-compliance-lead", "approved",
                        "Card and risk assessment complete; opsaudit DI=0.9375 "
                        "within threshold; human-in-the-loop enforced.",
                        [ev["id"]], actor)
    created.append(m["name"])

    # 2. Hiring Screen RAG Assistant — under review with a linked incident.
    m = reg.register("Hiring Screen RAG Assistant", "example-people-ops",
                     actor)
    reg.add_card(m["id"], HIRING_CARD, "example-people-ops", actor)
    reg.add_risk(m["id"], HIRING_RISK, "high", "example-risk-team", actor)
    summary, payload = evidence_mod.import_raimonitor_incident(
        _res("raimonitor-incident-example.json"))
    reg.attach_evidence(m["id"], "raimonitor_incident", summary, payload,
                        "example-ml-ops", actor)
    reg.set_status(m["id"], "under_review", actor)
    created.append(m["name"])

    # 3. Support Ticket Summarizer — draft with a card only.
    m = reg.register("Support Ticket Summarizer", "example-support-ops",
                     actor)
    reg.add_card(m["id"], SUPPORT_CARD, "example-support-ops", actor)
    created.append(m["name"])

    return created
