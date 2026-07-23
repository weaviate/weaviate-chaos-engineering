"""Receives Weaviate telemetry so chaos runs never reach the production endpoint.

Weaviate treats any response other than 200 as an error it logs on every push,
so this always answers 200. Payloads go to stdout: `docker compose logs
telemetry-sink` is the record of what a run would have reported.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            payload = json.dumps(json.loads(body))
        except ValueError:
            payload = body.decode("utf-8", "replace")
        print(f"telemetry {self.path} {payload}", flush=True)
        self._respond(b"{}")

    def do_GET(self):
        self._respond(b"")  # readiness probe

    def _respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # payloads are printed above, skip the default per-request line


if __name__ == "__main__":
    print(f"telemetry sink listening on :{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
