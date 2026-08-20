"""Ablage von Ereignissen, Zustand und kalibrierten Baselines.

SQLite reicht voellig: es geht um wenige Ereignisse pro Tag, und die Datei
laesst sich einfach sichern. Die Rohmesswerte landen bewusst nicht in der
Datenbank, sondern in einer separaten NDJSON-Aufzeichnung - die faellt
grosser aus und wird nur zum Nachjustieren gebraucht.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .alarm import AlarmEvent
from .baseline import BaselineSnapshot

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    type     TEXT    NOT NULL,
    level    TEXT    NOT NULL,
    mode     TEXT    NOT NULL,
    message  TEXT    NOT NULL,
    zones    TEXT    NOT NULL DEFAULT '[]',
    score    REAL    NOT NULL DEFAULT 0,
    links    TEXT    NOT NULL DEFAULT '[]',
    source   TEXT    NOT NULL DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);

CREATE TABLE IF NOT EXISTS baselines (
    link_id  TEXT PRIMARY KEY,
    median   REAL NOT NULL,
    scale    REAL NOT NULL,
    samples  INTEGER NOT NULL,
    updated  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Storage:
    """Thread-sichere, schlanke Persistenzschicht."""

    def __init__(self, directory: str | Path, filename: str = "wlanalarm.sqlite3") -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / filename
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # -- Ereignisse -------------------------------------------------------- #

    def add_event(self, event: AlarmEvent) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO events (ts, type, level, mode, message, zones, score, links, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ts,
                    event.type,
                    event.level,
                    event.mode,
                    event.message,
                    json.dumps(event.zones),
                    event.score,
                    json.dumps(event.links),
                    event.source,
                ),
            )
            self._connection.commit()

    def recent_events(self, limit: int = 100, min_level: str | None = None) -> list[dict]:
        query = "SELECT * FROM events"
        params: list = []
        if min_level:
            from .alarm import LEVEL_ORDER

            allowed = [
                name
                for name, rank in LEVEL_ORDER.items()
                if rank >= LEVEL_ORDER.get(min_level, 0)
            ]
            query += f" WHERE level IN ({','.join('?' * len(allowed))})"
            params.extend(allowed)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "zones": json.loads(row["zones"]),
                "links": json.loads(row["links"]),
            }
            for row in rows
        ]

    def purge_events(self, retention_days: int) -> int:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            cursor = self._connection.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._connection.commit()
        return cursor.rowcount

    # -- Baselines --------------------------------------------------------- #

    def save_baselines(self, snapshots: dict[str, BaselineSnapshot]) -> None:
        now = time.time()
        with self._lock:
            self._connection.executemany(
                "INSERT INTO baselines (link_id, median, scale, samples, updated)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(link_id) DO UPDATE SET"
                " median=excluded.median, scale=excluded.scale,"
                " samples=excluded.samples, updated=excluded.updated",
                [
                    (link_id, snap.median, snap.scale, snap.samples, snap.updated or now)
                    for link_id, snap in snapshots.items()
                ],
            )
            self._connection.commit()

    def load_baselines(self, max_age_days: float = 30.0) -> dict[str, BaselineSnapshot]:
        """Gespeicherte Baselines laden.

        Zu alte Werte werden verworfen: Moebel wandern, Geraete wechseln den
        Platz, und eine ein halbes Jahr alte Baseline beschreibt die Wohnung
        von damals.
        """
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM baselines WHERE updated >= ?", (cutoff,)
            ).fetchall()
        return {
            row["link_id"]: BaselineSnapshot(
                median=row["median"],
                scale=row["scale"],
                samples=row["samples"],
                updated=row["updated"],
            )
            for row in rows
        }

    # -- Zustand ----------------------------------------------------------- #

    def set_state(self, key: str, value) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._connection.commit()

    def get_state(self, key: str, default=None):
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:  # pragma: no cover - defensiv
            return default
