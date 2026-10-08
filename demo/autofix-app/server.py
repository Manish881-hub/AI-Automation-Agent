"""Closed-loop demo server: static login site + real auth backend.

Run:  python3 server.py 8921   (stdlib only, no dependencies)

Demo-only dev behavior: the auth module is reloaded on every login
request so an agent-applied patch takes effect WITHOUT restarting the
server — the browser re-test then exercises the fixed code in place.
"""

import importlib
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "backend"))

import auth


class Handler(BaseHTTPRequestHandler):
    server_version = "AutofixDemo/0.3"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (BASE / "frontend" / "index.html").read_bytes(), "text/html")
        elif self.path == "/dashboard":
            self._send(200, (BASE / "frontend" / "dashboard.html").read_bytes(), "text/html")
        else:
            self._send(404, b'{"error": "not found"}', "application/json")

    def do_POST(self):
        if self.path != "/api/login":
            self._send(404, b'{"error": "not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error": "invalid json"}', "application/json")
            return
        try:
            importlib.reload(auth)  # demo-only: pick up agent patches live
            result = auth.authenticate(
                payload.get("username", ""), payload.get("password", "")
            )
        except Exception as exc:
            body = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).encode()
            self._send(500, body, "application/json")
            return
        if not result.get("ok"):
            self._send(401, json.dumps(result).encode(), "application/json")
            return
        self._send(200, json.dumps(result).encode(), "application/json")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8921
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    print(f"serving autofix-app on 127.0.0.1:{port}")
