"""Loopback-only web UI and JSON API (standard library only)."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import engine, model, report

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "costa-pay.json")
APP_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
           "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
MAX_BODY = 256 * 1024
API_MAX_ITERATIONS = 50000
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}


class App:
    def __init__(self, sample=None):
        self.lock = threading.Lock()
        self.last = None
        self.sample = sample


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fair-sim"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, ctype, body, csp=APP_CSP):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, status, obj):
            self._send(status, "application/json; charset=utf-8", json.dumps(obj))

        def _host_ok(self):
            port = self.server.server_address[1]
            return self.headers.get("Host") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

        def _post_ok(self):
            if self.headers.get("X-FairSim-Request") != "1":
                return False
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == "http://" + (self.headers.get("Host") or "")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "text/plain; charset=utf-8", "bad host")
            path = self.path.split("?", 1)[0]
            if path == "/favicon.ico":
                return self._send(204, "image/x-icon", b"")
            if path in STATIC:
                name, ctype = STATIC[path]
                with open(os.path.join(WEB, name), "rb") as f:
                    return self._send(200, ctype, f.read())
            if path == "/api/sample":
                with open(app.sample or SAMPLE, "r", encoding="utf-8") as f:
                    return self._send(200, "application/json; charset=utf-8", f.read())
            if path in ("/report.html", "/report.md"):
                last = app.last
                if last is None:
                    return self._json(404, {"error": "run a simulation first"})
                if path == "/report.html":
                    return self._send(200, "text/html; charset=utf-8", report.html(last), REPORT_CSP)
                return self._send(200, "text/markdown; charset=utf-8", report.markdown(last))
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "text/plain; charset=utf-8", "bad host")
            if self.path != "/api/simulate":
                return self._json(404, {"error": "not found"})
            if not self._post_ok():
                return self._json(403, {"error": "forbidden"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "bad length"})
            if n <= 0 or n > MAX_BODY:
                return self._json(400, {"error": "request body must be 1 byte to %d bytes" % MAX_BODY})
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
                m = model.parse(data, max_iterations=API_MAX_ITERATIONS)
            except (ValueError, UnicodeDecodeError) as e:
                return self._json(400, {"error": str(e)})
            with app.lock:
                result = engine.analyse(m)
                app.last = result
            return self._json(200, result)

    return Handler


def create(port=0, sample=None):
    app = App(sample)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    srv.daemon_threads = True
    srv.app = app
    return srv
