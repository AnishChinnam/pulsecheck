"""Short, isolated SQLite transactions; callers run blocking work off the event loop."""
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import MonitorInput, ProbeResult


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS monitors (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE, expected_status INTEGER NOT NULL,
                    timeout_seconds REAL NOT NULL, attempts INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'pending',
                    failure_streak INTEGER NOT NULL DEFAULT 0,
                    success_streak INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, last_checked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id),
                    checked_at TEXT NOT NULL, ok INTEGER NOT NULL,
                    latency_ms REAL NOT NULL, status_code INTEGER,
                    attempts_used INTEGER NOT NULL, error TEXT
                );
                CREATE INDEX IF NOT EXISTS checks_by_monitor_time ON checks(monitor_id, checked_at);
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY, monitor_id INTEGER NOT NULL REFERENCES monitors(id),
                    opened_at TEXT NOT NULL, resolved_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident
                    ON incidents(monitor_id) WHERE resolved_at IS NULL;
                PRAGMA user_version=1;
            """)

    def add(self, monitor: MonitorInput) -> dict:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO monitors(name,url,expected_status,timeout_seconds,attempts,created_at) VALUES(?,?,?,?,?,?)",
                (monitor.name, monitor.url, monitor.expected_status, monitor.timeout_seconds, monitor.attempts, utcnow()),
            )
            return dict(db.execute("SELECT * FROM monitors WHERE id=?", (cursor.lastrowid,)).fetchone())

    def monitors(self, enabled_only=False) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM monitors" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id")
            return [dict(row) for row in rows]

    def set_enabled(self, monitor_id: int, enabled: bool) -> dict | None:
        with self.connect() as db:
            db.execute("UPDATE monitors SET enabled=? WHERE id=?", (int(enabled), monitor_id))
            row = db.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
            return dict(row) if row else None

    def record(self, result: ProbeResult, failure_threshold=2, recovery_threshold=2) -> dict:
        with self.connect() as db:
            # The sample, state transition and incident update commit together.
            db.execute("BEGIN IMMEDIATE")
            monitor = db.execute("SELECT * FROM monitors WHERE id=?", (result.monitor_id,)).fetchone()
            if monitor is None:
                raise KeyError(result.monitor_id)
            db.execute(
                "INSERT INTO checks(monitor_id,checked_at,ok,latency_ms,status_code,attempts_used,error) VALUES(?,?,?,?,?,?,?)",
                (result.monitor_id, result.checked_at, int(result.ok), result.latency_ms, result.status_code, result.attempts_used, result.error),
            )
            failures = 0 if result.ok else monitor["failure_streak"] + 1
            successes = monitor["success_streak"] + 1 if result.ok else 0
            if result.ok:
                state = "down" if monitor["state"] == "down" and successes < recovery_threshold else "up"
                if state == "up":
                    db.execute("UPDATE incidents SET resolved_at=? WHERE monitor_id=? AND resolved_at IS NULL", (result.checked_at, result.monitor_id))
            else:
                state = "down" if failures >= failure_threshold or monitor["state"] == "down" else "degraded"
                if state == "down":
                    db.execute("INSERT OR IGNORE INTO incidents(monitor_id,opened_at) VALUES(?,?)", (result.monitor_id, result.checked_at))
            db.execute(
                "UPDATE monitors SET state=?,failure_streak=?,success_streak=?,last_checked_at=? WHERE id=?",
                (state, failures, successes, result.checked_at, result.monitor_id),
            )
            return {"monitor_id": result.monitor_id, "state": state, "ok": result.ok, "attempts_used": result.attempts_used}

    def history(self, monitor_id: int, limit=100) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM checks WHERE monitor_id=? ORDER BY id DESC LIMIT ?", (monitor_id, limit))]

    def incidents(self, limit=100) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT i.*,m.name FROM incidents i JOIN monitors m ON m.id=i.monitor_id ORDER BY i.id DESC LIMIT ?", (limit,))]

    def summary(self, now: str | None = None) -> dict:
        as_of = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
        cutoff = (as_of - timedelta(hours=24)).isoformat(timespec="milliseconds")
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM monitors ORDER BY id")]
            for monitor in rows:
                samples = db.execute(
                    "SELECT ok,latency_ms FROM checks WHERE monitor_id=? AND checked_at>=? AND checked_at<=? ORDER BY latency_ms",
                    (monitor["id"], cutoff, as_of.isoformat(timespec="milliseconds")),
                ).fetchall()
                successes = [r["latency_ms"] for r in samples if r["ok"]]
                monitor["checks_24h"] = len(samples)
                monitor["success_rate_24h"] = round(len(successes) / len(samples) * 100, 2) if samples else None
                monitor["p95_ms"] = round(successes[math.ceil(.95 * len(successes)) - 1], 2) if successes else None
            open_count = db.execute("SELECT COUNT(*) FROM incidents WHERE resolved_at IS NULL").fetchone()[0]
        return {"as_of": as_of.isoformat(), "window_hours": 24, "open_incidents": open_count, "monitors": rows}
