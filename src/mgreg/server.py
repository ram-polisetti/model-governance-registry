"""Read-mostly web dashboard for the registry (stdlib only).

Serves a model list and per-model detail pages: card, risk assessment,
approval, evidence, and audit history. All dynamic values are
HTML-escaped; there are no forms and no writes — governance actions
stay on the CLI so every change is an explicit, attributed command.
"""

from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

from .store import Registry

STATUS_COLORS = {
    "draft": "#8a8a8e",
    "under_review": "#b7791f",
    "approved": "#1d7a3a",
    "rejected": "#b3261e",
    "retired": "#5f5f63",
}

RISK_COLORS = {"low": "#1d7a3a", "medium": "#b7791f", "high": "#b3261e"}

CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
color:#1d1d1f;background:#fbfbfd;margin:0;line-height:1.5}
.wrap{max-width:860px;margin:0 auto;padding:32px 20px 64px}
a{color:#0066cc;text-decoration:none}a:hover{text-decoration:underline}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;
font-size:12px;font-weight:600;vertical-align:middle}
.card{background:#fff;border:1px solid #e5e5ea;border-radius:12px;
padding:20px 24px;margin:16px 0}
h1{font-size:32px;letter-spacing:-0.5px}h2{font-size:20px;margin-top:0}
h3{font-size:15px;color:#6e6e73;text-transform:uppercase;letter-spacing:.06em}
table{border-collapse:collapse;width:100%}td,th{padding:8px 10px;
border-bottom:1px solid #eee;text-align:left;font-size:14px}
pre{background:#f5f5f7;padding:12px;border-radius:8px;overflow:auto;font-size:13px}
.meta{color:#6e6e73;font-size:14px}
"""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _badge(text: str, color: str) -> str:
    return (f'<span class="badge" style="background:{color}">'
            f"{esc(text)}</span>")


def _page(title: str, body: str) -> bytes:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — mgreg</title><style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>""".encode("utf-8")


def _sections(content: dict, order: tuple[str, ...]) -> str:
    parts = []
    for key in order:
        if key in content:
            parts.append(f"<h3>{esc(key.replace('_', ' '))}</h3>"
                         f"<p>{esc(content[key])}</p>")
    extra = [k for k in content if k not in order]
    for key in extra:
        parts.append(f"<h3>{esc(key.replace('_', ' '))}</h3>"
                     f"<p>{esc(content[key])}</p>")
    return "\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    db_path: str = ""  # set by serve()

    @property
    def registry(self) -> Registry:
        # A fresh connection per request: SQLite connections cannot be
        # shared across threads, and the server handles requests on its
        # own thread.
        return Registry(self.db_path)

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])
        try:
            if path == "/":
                self._send(_page("Models", self._index()))
            elif path.startswith("/model/"):
                ident = path[len("/model/"):]
                self._send(_page("Model", self._detail(ident)))
            else:
                self._send(_page("Not found", "<h1>404</h1>"), 404)
        except ValueError as exc:
            self._send(_page("Error", f"<h1>Error</h1><p>{esc(exc)}</p>"), 404)

    def _index(self) -> str:
        rows = []
        for m in self.registry.list_models():
            risk = self.registry.latest_risk(m["id"])
            risk_badge = (_badge(risk["overall_risk"],
                                 RISK_COLORS.get(risk["overall_risk"], "#888"))
                          if risk else '<span class="meta">no assessment</span>')
            rows.append(
                f'<tr><td><a href="/model/{esc(m["id"])}">{esc(m["name"])}</a></td>'
                f"<td>{_badge(m['status'], STATUS_COLORS.get(m['status'], '#888'))}</td>"
                f"<td>{risk_badge}</td><td class='meta'>{esc(m['owner'])}</td></tr>")
        table = ("<table><tr><th>Model</th><th>Status</th><th>Risk</th>"
                 "<th>Owner</th></tr>" + "".join(rows) + "</table>"
                 if rows else "<p>No models registered yet.</p>")
        return (f"<h1>Model governance registry</h1>"
                f"<p class='meta'>Model cards, NIST AI RMF risk assessments, "
                f"approvals, and a tamper-evident audit trail.</p>{table}")

    def _detail(self, ident: str) -> str:
        reg = self.registry
        model = reg.get_model(ident)
        card = reg.latest_card(model["id"])
        risk = reg.latest_risk(model["id"])
        approval = reg.latest_approval(model["id"])
        evidence = reg.list_evidence(model["id"])
        events = reg.history(model["id"])

        parts = [f"<p><a href='/'>&larr; all models</a></p>",
                 f"<h1>{esc(model['name'])}</h1>",
                 _badge(model["status"],
                        STATUS_COLORS.get(model["status"], "#888")),
                 f"<p class='meta'>owner: {esc(model['owner'])} · "
                 f"created: {esc(model['created_at'])}</p>"]

        if card:
            parts.append("<div class='card'><h2>Model card "
                         f"(v{card['version']})</h2>"
                         + _sections(card["content"],
                                     ("purpose", "intended_use", "out_of_scope",
                                      "training_data", "evaluation",
                                      "limitations")) + "</div>")
        else:
            parts.append("<div class='card'><h2>Model card</h2>"
                         "<p class='meta'>None recorded.</p></div>")

        if risk:
            parts.append("<div class='card'><h2>Risk assessment "
                         f"(v{risk['version']}) "
                         + _badge(risk["overall_risk"],
                                  RISK_COLORS.get(risk["overall_risk"], "#888"))
                         + "</h2>"
                         + _sections(risk["content"],
                                     ("govern", "map", "measure", "manage"))
                         + "</div>")

        if approval:
            ev_refs = ", ".join(esc(e) for e in approval["evidence_ids"]) or "—"
            parts.append("<div class='card'><h2>Approval "
                         f"(v{approval['version']})</h2>"
                         f"<p><strong>{esc(approval['decision'])}</strong> by "
                         f"{esc(approval['approver'])} · "
                         f"{esc(approval['created_at'])}</p>"
                         f"<p>{esc(approval['rationale'])}</p>"
                         f"<p class='meta'>evidence: {ev_refs}</p></div>")

        if evidence:
            items = "".join(
                f"<li><strong>{esc(e['kind'])}</strong> "
                f"<span class='meta'>{esc(e['id'])} · {esc(e['created_at'])}</span>"
                f"<pre>{esc(__import__('json').dumps(e['summary'], indent=2))}</pre></li>"
                for e in evidence)
            parts.append(f"<div class='card'><h2>Evidence "
                         f"({len(evidence)})</h2><ul>{items}</ul></div>")

        hist = "".join(
            f"<tr><td>#{e['seq']}</td><td>{esc(e['created_at'])}</td>"
            f"<td>{esc(e['event_type'])}</td><td>{esc(e['actor'])}</td></tr>"
            for e in events)
        parts.append("<div class='card'><h2>Audit trail "
                     f"({len(events)} events)</h2>"
                     f"<table><tr><th>#</th><th>When</th><th>Event</th>"
                     f"<th>Actor</th></tr>{hist}</table></div>")
        return "\n".join(parts)


def serve(db_path: str, port: int = 8080) -> None:
    """Serve the dashboard. Blocks until interrupted."""
    Handler.db_path = db_path
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"mgreg dashboard at http://127.0.0.1:{port}/ "
          f"(db: {db_path}) — Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
