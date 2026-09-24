from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from jevops.models import LogEvent, TriageResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT 'unknown',
    env TEXT NOT NULL DEFAULT 'unknown',
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '{}',
    seen_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS triages (
    event_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_conf REAL NOT NULL,
    severity_probs TEXT NOT NULL,
    category TEXT NOT NULL,
    category_probs TEXT NOT NULL,
    needs_human REAL NOT NULL,
    novelty REAL NOT NULL,
    urgency REAL NOT NULL,
    is_recovery REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(id)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    composite REAL NOT NULL DEFAULT 0,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    dedup_key TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    event_id TEXT NOT NULL,
    paged_at INTEGER NOT NULL,
    resolved_at INTEGER
);
CREATE TABLE IF NOT EXISTS checkpoints (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS log_lines (
    pk INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT '',
    ts_epoch REAL NOT NULL DEFAULT 0,
    service TEXT NOT NULL DEFAULT 'unknown',
    env TEXT NOT NULL DEFAULT 'unknown',
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_log_lines_epoch ON log_lines (ts_epoch);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_event(self, event: LogEvent) -> bool:
        cur = self._conn.execute(
            (
                "INSERT OR IGNORE INTO events (id, ts, service, env, level, message, fields, seen_at) "
                "VALUES (?,?,?,?,?,?,?,?)"
            ),
            (
                event.id,
                event.ts,
                event.service,
                event.env,
                event.level,
                event.message,
                json.dumps(event.fields),
                int(time.time()),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def event_seen(self, event_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone()
        return row is not None

    def save_triage(self, triage: TriageResult) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO triages
            (event_id, model, severity, severity_conf, severity_probs, category, category_probs,
             needs_human, novelty, urgency, is_recovery, latency_ms, input_tokens, output_tokens, at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                triage.event_id,
                triage.model,
                triage.severity,
                triage.severity_conf,
                json.dumps(triage.severity_probs),
                triage.category,
                json.dumps(triage.category_probs),
                triage.needs_human,
                triage.novelty,
                triage.urgency,
                triage.is_recovery,
                triage.latency_ms,
                triage.input_tokens,
                triage.output_tokens,
                int(time.time()),
            ),
        )
        self._conn.commit()

    def get_triage(self, event_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM triages WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["severity_probs"] = json.loads(d["severity_probs"])
        d["category_probs"] = json.loads(d["category_probs"])
        return d

    def record_decision(self, event_id: str, action: str, reason: str, composite: float) -> None:
        self._conn.execute(
            "INSERT INTO decisions (event_id, action, reason, composite, at) VALUES (?,?,?,?,?)",
            (event_id, action, reason, composite, int(time.time())),
        )
        self._conn.commit()

    def mark_paged(self, event_id: str, dedup_key: str, service: str = "", category: str = "") -> None:
        row = self._conn.execute(
            "SELECT service, category FROM triages "
            "JOIN events ON events.id = triages.event_id WHERE triages.event_id = ?",
            (event_id,),
        ).fetchone()
        svc = service or (row["service"] if row else "")
        cat = category or (row["category"] if row else "")
        self._conn.execute(
            "INSERT OR REPLACE INTO pages (dedup_key, service, category, event_id, paged_at, resolved_at) "
            "VALUES (?,?,?,?,?,NULL)",
            (dedup_key, svc, cat, event_id, int(time.time())),
        )
        self._conn.commit()

    def mark_resolved(self, dedup_key: str) -> None:
        self._conn.execute(
            "UPDATE pages SET resolved_at = ? WHERE dedup_key = ? AND resolved_at IS NULL",
            (int(time.time()), dedup_key),
        )
        self._conn.commit()

    def page_active(self, service: str, category: str, cooldown_seconds: int) -> bool:
        row = self._conn.execute(
            "SELECT paged_at, resolved_at FROM pages "
            "WHERE service = ? AND category = ? AND resolved_at IS NULL "
            "ORDER BY paged_at DESC LIMIT 1",
            (service, category),
        ).fetchone()
        if row is None:
            return False
        return int(time.time()) - int(row["paged_at"]) < cooldown_seconds

    def paged_for(self, service: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM pages WHERE service = ? AND resolved_at IS NULL", (service,)).fetchone()
        return row is not None

    def recent_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT d.id, d.event_id, d.action, d.reason, d.composite, d.at,
                      e.ts, e.service, e.env, e.level, e.message,
                      t.model, t.severity, t.severity_conf, t.category, t.category_probs,
                      t.needs_human, t.novelty, t.urgency, t.is_recovery, t.latency_ms,
                      t.input_tokens, t.output_tokens
               FROM decisions d
               JOIN events e ON e.id = d.event_id
               LEFT JOIN triages t ON t.event_id = d.event_id
               ORDER BY d.at DESC, d.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["category_probs"] = json.loads(d["category_probs"]) if d.get("category_probs") else {}
            out.append(d)
        return out

    def checkpoint_get(self, name: str) -> str | None:
        row = self._conn.execute("SELECT value FROM checkpoints WHERE name = ?", (name,)).fetchone()
        return row["value"] if row else None

    def checkpoint_set(self, name: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO checkpoints (name, value) VALUES (?,?)", (name, value))
        self._conn.commit()

    def add_log_line(self, event: LogEvent) -> bool:
        from jevops.chunks import parse_ts

        ts = parse_ts(event.ts)
        cur = self._conn.execute(
            "INSERT INTO log_lines (fingerprint, ts, ts_epoch, service, env, level, message, fields) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                event.id,
                event.ts,
                ts.timestamp() if ts else 0.0,
                event.service,
                event.env,
                event.level,
                event.message,
                json.dumps(event.fields),
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def lines_between(self, start: str, end: str) -> list[LogEvent]:
        from jevops.chunks import parse_ts

        query = "SELECT * FROM log_lines"
        clauses: list[str] = []
        params: list[float] = []
        start_dt = parse_ts(start) if start else None
        end_dt = parse_ts(end) if end else None
        if start_dt:
            clauses.append("ts_epoch >= ?")
            params.append(start_dt.timestamp())
        if end_dt:
            clauses.append("ts_epoch < ?")
            params.append(end_dt.timestamp())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts_epoch ASC, pk ASC"
        rows = self._conn.execute(query, params).fetchall()
        return [
            LogEvent(
                id=row["fingerprint"],
                ts=row["ts"],
                service=row["service"],
                env=row["env"],
                level=row["level"],
                message=row["message"],
                fields=json.loads(row["fields"]),
            )
            for row in rows
        ]
