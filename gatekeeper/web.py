"""Local console: a small JSON API over the service layer plus the static app.

Approvals from the console need the one-time token printed in the terminal
when the console starts, so an agent that can call the HTTP API or MCP tools
still cannot approve.
"""

from __future__ import annotations

import json
import secrets
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import service as svc
from .flagsmith import FlagsmithError, Settings
from .ledger import Record

STATIC = Path(__file__).parent / "static"


def make_handler(token: str, replay: str | None):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATIC), **kw)

        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/api/state":
                if replay:
                    return self._json(json.loads(Path(replay).read_text(encoding="utf-8")))
                s = Settings.from_env()
                return self._json({"mode": "live", "environment": s.environment_key, "dashboard": s.dashboard_url,
                                   "experiments": [r.data for r in Record.all()]})
            if u.path == "/replay.json" and replay:
                self.path = "/"
                body = Path(replay).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            parts = u.path.strip("/").split("/")
            if replay:
                return self._json({"error": "This console is showing a recorded run."}, 400)
            if len(parts) != 3 or parts[0] != "api":
                return self._json({"error": "not found"}, 404)
            action, slug = parts[1], parts[2]
            qs = parse_qs(u.query)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            try:
                if action in ("approve", "approve-ship"):
                    if not secrets.compare_digest(body.get("token", "") or qs.get("token", [""])[0], token):
                        return self._json({"refused": True, "reason": "Approvals need the console token printed in the terminal that started `gk serve`."}, 403)
                    out = svc.approve(slug, via="console", kind="ship" if action == "approve-ship" else "start",
                                      by=body.get("by") or None)
                elif action == "preflight":
                    rec = Record.load(slug)
                    out = svc.run_preflight(rec.data["code_path"], svc.client(), rec)
                elif action == "request":
                    out = svc.request_approval(slug, "person")
                elif action == "start":
                    out = svc.start(slug, "person")
                elif action == "verdict":
                    out = svc.verdict(slug, "person")
                elif action == "request-ship":
                    out = svc.request_ship(slug, "person")
                elif action == "ship":
                    out = svc.ship(slug, "person")
                else:
                    return self._json({"error": f"unknown action {action}"}, 404)
                return self._json({"ok": True, "result": out, "experiment": Record.load(slug).data})
            except svc.GateRefused as e:
                return self._json({"refused": True, "reason": str(e), "experiment": Record.load(slug).data}, 409)
            except FlagsmithError as e:
                return self._json({"error": str(e)}, 502)
            except FileNotFoundError as e:
                return self._json({"error": str(e)}, 404)

    return Handler


def run(port: int = 4810, replay: str | None = None, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(16)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, replay))
    url = f"http://127.0.0.1:{port}/" + ("" if replay else f"#token={token}")
    print(f"Gatekeeper console: {url}")
    if not replay:
        print("The token in this link is what lets a person approve from the browser. Do not paste it into an agent.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    _ = HTTPStatus
