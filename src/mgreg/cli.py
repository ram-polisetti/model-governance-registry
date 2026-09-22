"""Command-line interface for the model governance registry."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .store import Registry, RegistryError
from . import evidence as evidence_mod
from . import seed as seed_mod
from . import server as server_mod
from . import sweeper as sweeper_mod


def default_db() -> str:
    return os.environ.get(
        "MGREG_DB", str(Path.home() / ".mgreg" / "registry.db"))


def _registry(args: argparse.Namespace) -> Registry:
    return Registry(args.db)


def _load_json_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RegistryError(f"{path}: top level must be a JSON object")
    return data


def cmd_register(args: argparse.Namespace) -> int:
    reg = _registry(args)
    model = reg.register(args.name, args.owner, args.actor)
    print(f"registered {model['name']} (id={model['id']}) status={model['status']}")
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    reg = _registry(args)
    card = reg.add_card(args.model, _load_json_file(args.file), args.by,
                        args.actor)
    print(f"card v{card['version']} recorded for {args.model}")
    return 0


def cmd_assess(args: argparse.Namespace) -> int:
    reg = _registry(args)
    risk = reg.add_risk(args.model, _load_json_file(args.file),
                        args.overall_risk, args.by, args.actor)
    print(f"risk assessment v{risk['version']} recorded for {args.model} "
          f"(overall: {risk['overall_risk']})")
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    reg = _registry(args)
    model = reg.set_status(args.model, "under_review", args.actor)
    print(f"{model['name']} submitted for review")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    reg = _registry(args)
    approval = reg.record_approval(
        args.model, args.approver, args.decision, args.rationale,
        args.evidence or [], args.actor)
    print(f"decision recorded: {approval['decision']} "
          f"(v{approval['version']}, approver={approval['approver']})")
    return 0


def cmd_retire(args: argparse.Namespace) -> int:
    reg = _registry(args)
    model = reg.set_status(args.model, "retired", args.actor)
    print(f"{model['name']} retired")
    return 0


def cmd_evidence_audit(args: argparse.Namespace) -> int:
    reg = _registry(args)
    summary, payload = evidence_mod.import_opsaudit_audit(args.file)
    rec = reg.attach_evidence(args.model, "opsaudit_audit", summary,
                              payload, args.by, args.actor)
    print(f"opsaudit audit attached as evidence {rec['id']} "
          f"(DI={summary['disparate_impact_ratio']}, "
          f"gate={summary['gate_status']})")
    return 0


def cmd_evidence_incident(args: argparse.Namespace) -> int:
    reg = _registry(args)
    summary, payload = evidence_mod.import_raimonitor_incident(args.file)
    rec = reg.attach_evidence(args.model, "raimonitor_incident", summary,
                              payload, args.by, args.actor)
    print(f"rai-monitor incident {summary['incident_id']} attached as "
          f"evidence {rec['id']} (status={summary['status']})")
    return 0


def cmd_evidence_note(args: argparse.Namespace) -> int:
    reg = _registry(args)
    rec = reg.attach_evidence(args.model, "note",
                              {"title": args.title},
                              {"title": args.title, "body": args.body},
                              args.by, args.actor)
    print(f"note attached as evidence {rec['id']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    reg = _registry(args)
    model = reg.get_model(args.model)
    view = {
        "model": model,
        "card": reg.latest_card(model["id"]),
        "risk": reg.latest_risk(model["id"]),
        "approval": reg.latest_approval(model["id"]),
        "evidence": reg.list_evidence(model["id"]),
    }
    if args.json:
        print(json.dumps(view, indent=2, sort_keys=True, default=str))
    else:
        _print_model(view)
    return 0


def _print_model(view: dict) -> None:
    model = view["model"]
    print(f"{model['name']}  [{model['status']}]")
    print(f"  owner: {model['owner']}   created: {model['created_at']}")
    card = view["card"]
    print(f"  card: {'v' + str(card['version']) if card else '—'}")
    risk = view["risk"]
    print(f"  risk: {'v' + str(risk['version']) + ' (' + risk['overall_risk'] + ')' if risk else '—'}")
    approval = view["approval"]
    if approval:
        print(f"  approval: {approval['decision']} by {approval['approver']} "
              f"(v{approval['version']})")
    else:
        print("  approval: —")
    ev = view["evidence"]
    print(f"  evidence: {len(ev)} item(s)")
    for item in ev:
        print(f"    - {item['kind']}: {item['id']}")


def cmd_list(args: argparse.Namespace) -> int:
    reg = _registry(args)
    models = reg.list_models(args.status)
    if args.json:
        print(json.dumps(models, indent=2, sort_keys=True, default=str))
    else:
        for m in models:
            print(f"{m['name']:<40} {m['status']:<12} {m['owner']}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    reg = _registry(args)
    events = reg.history(args.model)
    if args.json:
        print(json.dumps(events, indent=2, sort_keys=True, default=str))
    else:
        for e in events:
            print(f"#{e['seq']} {e['created_at']} {e['event_type']:<18} "
                  f"actor={e['actor']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    reg = _registry(args)
    result = reg.verify()
    print(f"audit chain: {'OK' if result['ok'] else 'BROKEN'} "
          f"({result['records']} events)")
    for err in result["errors"]:
        print(f"  ! {err}")
    return 0 if result["ok"] else 1


def cmd_serve(args: argparse.Namespace) -> int:
    server_mod.serve(args.db, args.port)
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    reg = _registry(args)
    names = seed_mod.seed(reg, args.actor)
    for n in names:
        print(f"seeded: {n}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    reg = _registry(args)
    cfg = (sweeper_mod.load_config(args.config) if args.config
           else sweeper_mod.default_config())
    report = sweeper_mod.sweep(reg, cfg)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"swept {report['models_checked']} approved model(s): "
              f"{report['flags']} flag(s), "
              f"{report['queue_items_created']} new re-review item(s), "
              f"{report['models_demoted']} demoted")
        for m in report["models"]:
            for flag in m["flags"]:
                print(f"  ! {m['model']}: [{flag['rule']}] {flag['reason']}")
            for note in m["notes"]:
                print(f"  - {m['model']}: {note}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    reg = _registry(args)
    items = reg.list_re_review(args.status or "open")
    if args.json:
        print(json.dumps(items, indent=2, sort_keys=True, default=str))
    else:
        if not items:
            print("re-review queue is empty")
            return 0
        for it in items:
            model = reg.get_model(it["model_id"])
            print(f"{it['id']}  [{it['status']}] {model['name']}  "
                  f"rule={it['rule']}")
            print(f"    reason: {it['reason']}")
            print(f"    detected: {it['detected_at']}")
            if it["status"] == "resolved":
                res = it.get("resolution") or {}
                print(f"    resolved: {it['resolved_at']} by "
                      f"{it['resolved_by']} ({res.get('decision')})")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    reg = _registry(args)
    baseline = None
    if args.decision == "waive":
        cfg = (sweeper_mod.load_config(args.config) if args.config
               else sweeper_mod.default_config())
        item = reg.get_queue_item(args.id)
        baseline = sweeper_mod.build_waiver_baseline(
            reg, cfg, item["model_id"])
    item = reg.resolve_re_review(
        args.id, args.decision, args.rationale, args.actor,
        approver=args.approver, waiver_baseline=baseline,
        evidence_ids=args.evidence)
    print(f"re-review {item['id']} resolved: {args.decision}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mgreg",
        description="Model governance registry: cards, risk assessments, "
                    "approvals, tamper-evident audit trail.")
    p.add_argument("--db", default=default_db(), help="registry database path")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_actor(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--actor", default=os.environ.get("USER", "operator"),
                        help="who is performing this action")

    s = sub.add_parser("register", help="register a new model")
    s.add_argument("--name", required=True)
    s.add_argument("--owner", required=True)
    add_actor(s)
    s.set_defaults(func=cmd_register)

    s = sub.add_parser("card", help="add a model card version (JSON file)")
    s.add_argument("--model", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--by", required=True, help="card author")
    add_actor(s)
    s.set_defaults(func=cmd_card)

    s = sub.add_parser("assess", help="add a risk assessment version (JSON file)")
    s.add_argument("--model", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--overall-risk", required=True,
                   choices=("low", "medium", "high"))
    s.add_argument("--by", required=True, help="assessor")
    add_actor(s)
    s.set_defaults(func=cmd_assess)

    s = sub.add_parser("submit", help="submit a draft model for review")
    s.add_argument("--model", required=True)
    add_actor(s)
    s.set_defaults(func=cmd_submit)

    s = sub.add_parser("approve", help="record an approval decision")
    s.add_argument("--model", required=True)
    s.add_argument("--approver", required=True)
    s.add_argument("--decision", required=True,
                   choices=("approved", "rejected"))
    s.add_argument("--rationale", required=True)
    s.add_argument("--evidence", nargs="*", default=[],
                   help="evidence ids supporting the decision")
    add_actor(s)
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("retire", help="retire an approved model")
    s.add_argument("--model", required=True)
    add_actor(s)
    s.set_defaults(func=cmd_retire)

    ev = sub.add_parser("evidence", help="attach evidence to a model")
    evsub = ev.add_subparsers(dest="ev_command", required=True)

    s = evsub.add_parser("attach-audit",
                         help="import an opsaudit audit result (JSON)")
    s.add_argument("--model", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--by", required=True)
    add_actor(s)
    s.set_defaults(func=cmd_evidence_audit)

    s = evsub.add_parser("attach-incident",
                         help="link a rai-monitor incident record (JSON)")
    s.add_argument("--model", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--by", required=True)
    add_actor(s)
    s.set_defaults(func=cmd_evidence_incident)

    s = evsub.add_parser("attach-note", help="attach a free-text note")
    s.add_argument("--model", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body", required=True)
    s.add_argument("--by", required=True)
    add_actor(s)
    s.set_defaults(func=cmd_evidence_note)

    s = sub.add_parser("show", help="show a model's full record")
    s.add_argument("--model", required=True)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("list", help="list registered models")
    s.add_argument("--status", default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("history", help="show the audit trail")
    s.add_argument("--model", default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("verify", help="verify the audit chain")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("serve", help="serve the read-mostly web dashboard")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("seed", help="seed the demo registry (3 example models)")
    add_actor(s)
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("sweep",
                       help="run the stale-approval sweeper over approved models")
    s.add_argument("--config", default=None,
                   help="sweeper config file (JSON; built-in defaults used if omitted)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sweep)

    s = sub.add_parser("queue", help="list the re-review queue")
    s.add_argument("--status", default="open",
                   choices=("open", "resolved", "all"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_queue)

    s = sub.add_parser("resolve", help="resolve a re-review queue item")
    s.add_argument("--id", required=True, help="queue item id")
    s.add_argument("--decision", required=True,
                   choices=("reapprove", "retire", "waive"))
    s.add_argument("--rationale", required=True)
    s.add_argument("--approver", default=None,
                   help="required for reapprove")
    s.add_argument("--evidence", nargs="*", default=None,
                   help="evidence ids the fresh approval cites "
                        "(reapprove only; default: all current evidence)")
    s.add_argument("--config", default=None,
                   help="sweeper config (used to baseline a waiver)")
    add_actor(s)
    s.set_defaults(func=cmd_resolve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
