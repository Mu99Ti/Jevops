import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jevops.cli import main


class _JevServer(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": {
                    "severity": {
                        "type": "choice",
                        "choice": "warning",
                        "probabilities": {"info": 0, "warning": 1, "critical": 0},
                        "confidence": 0.9,
                    },
                    "category": {
                        "type": "choice",
                        "choice": "capacity",
                        "probabilities": {
                            "deployment": 0,
                            "capacity": 1,
                            "dependency": 0,
                            "application_bug": 0,
                            "security": 0,
                            "data": 0,
                            "unknown": 0,
                        },
                        "confidence": 0.8,
                    },
                    "urgency": {"type": "score", "score": 1.0, "probabilities": {"1": 1}},
                    "needs_human": {"noul": 0.2},
                    "novelty": {"noul": 0.3},
                    "recovery": {"noul": 0.05},
                },
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


class _InfraServer(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/datasources/name/"):
            body = json.dumps({"message": "not found"}).encode()
            self.send_response(404)
        elif self.path == "/api/health":
            body = json.dumps({"database": "ok"}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"version": {"number": "8.15.0"}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_PUT(self) -> None:
        if self.path == "/jevops-decisions":
            self.send_response(200)
            body = json.dumps({"acknowledged": True}).encode()
        elif self.path.startswith("/api/datasources/"):
            body = json.dumps({"datasource": {"uid": "ds1", "id": 1}}).encode()
            self.send_response(200)
        elif self.path.startswith("/api/v1/provisioning/alert-rules/"):
            body = json.dumps({"uid": "jevops-critical-pages"}).encode()
            self.send_response(200)
        else:
            body = json.dumps({"uid": "jevops"}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/api/datasources":
            body = json.dumps({"datasource": {"uid": "ds1", "id": 1}, "id": 1, "message": "created"}).encode()
            self.send_response(200)
        elif self.path == "/api/folders":
            body = json.dumps({"uid": "jevops", "title": "Jevops"}).encode()
            self.send_response(200)
        elif self.path == "/api/v1/provisioning/contact-points":
            body = json.dumps({"uid": "cp1"}).encode()
            self.send_response(200)
        elif self.path == "/api/dashboards/db":
            body = json.dumps({"uid": "jevops-sre-triage", "version": 1}).encode()
            self.send_response(200)
        elif self.path == "/api/v1/provisioning/alert-rules":
            body = json.dumps({"message": "already exists"}).encode()
            self.send_response(412)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            body = b"{}"
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _serve(server_cls, handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_replay_command_end_to_end(tmp_path, monkeypatch, capsys):
    jev_server, jev_url = _serve(ThreadingHTTPServer, _JevServer)
    monkeypatch.setenv("TYPESAFE_BASE_URL", jev_url)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEVOPS_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("JEVOPS_SERVICES", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("JEVOPS_DRY_RUN", "1")
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {"@timestamp": "2026-09-24T13:40:12Z", "service": "api", "level": "ERROR", "message": "disk almost full"}
        )
        + "\n"
    )
    try:
        code = main(["replay", str(path)])
    finally:
        jev_server.shutdown()
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"] == 1
    assert payload["decisions"][0]["action"] == "digest"
    assert payload["decisions"][0]["triage"]["severity"] == "warning"


def test_provision_command_creates_es_and_grafana_resources(monkeypatch, capsys):
    infra, infra_url = _serve(ThreadingHTTPServer, _InfraServer)
    monkeypatch.setenv("ES_URL", infra_url)
    monkeypatch.setenv("GRAFANA_URL", infra_url)
    monkeypatch.setenv("GRAFANA_TOKEN", "tok")
    monkeypatch.setenv("JEVOPS_WEBHOOK_URL", infra_url + "/hook")
    monkeypatch.setenv("JEVOPS_DB", "/tmp/jevops-provision-test.db")
    monkeypatch.setenv("JEVOPS_SERVICES", "/tmp/jevops-missing.toml")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("ES_API_KEY", raising=False)
    try:
        code = main(["provision"])
    finally:
        infra.shutdown()
    assert code == 0
    out = capsys.readouterr().out
    assert "datasource" in out
    assert "dashboard" in out
    assert "alert_rule" in out
