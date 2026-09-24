from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from jevops.pipeline import Pipeline


def _handler_class(pipeline: Pipeline) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _respond(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/healthz":
                self._respond(200, {"status": "ok"})
            elif parsed.path == "/decisions":
                query = urllib.parse.parse_qs(parsed.query)
                limit = int(query.get("limit", ["50"])[0])
                self._respond(200, pipeline.store.recent_decisions(limit=limit))
            else:
                self._respond(404, {"error": "not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            if self.path != "/ingest":
                self._respond(404, {"error": "not found"})
                return
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._respond(400, {"error": "invalid json"})
                return
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                events = data["events"]
            elif isinstance(data, list):
                events = data
            elif isinstance(data, dict):
                events = [data]
            else:
                self._respond(400, {"error": "expected object or list"})
                return
            decisions = pipeline.process_events(events)
            self._respond(200, {"accepted": len(events), "decisions": [d.to_dict() for d in decisions]})

        def log_message(self, *args: object) -> None:
            return

    return Handler


def build_server(pipeline: Pipeline, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _handler_class(pipeline))
    server.daemon_threads = True
    return server
