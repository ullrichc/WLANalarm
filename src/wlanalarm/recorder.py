"""Rohmesswerte als NDJSON mitschreiben.

Eine Zeile je Messzyklus. Das Format liest ``wlanalarm replay`` wieder ein,
sodass sich Schwellen an echten Daten nachjustieren lassen, ohne tagelang
neu messen zu muessen.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .model import Scan

log = logging.getLogger(__name__)


class Recorder:
    """Schreibt Scans in eine taeglich rollierende Datei."""

    def __init__(self, directory: str | Path, retention_days: int = 3, compress: bool = False) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self.compress = compress
        self._handle = None
        self._current_day = ""

    def _rotate(self, ts: float) -> None:
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        if day == self._current_day and self._handle is not None:
            return
        self.close()
        suffix = ".ndjson.gz" if self.compress else ".ndjson"
        path = self.directory / f"samples-{day}{suffix}"
        opener = gzip.open if self.compress else open
        self._handle = opener(path, "at", encoding="utf-8")
        self._current_day = day
        log.info("Aufzeichnung laeuft nach %s", path)
        self.purge()

    def write(self, scan: Scan) -> None:
        self._rotate(scan.ts)
        record = {"ts": scan.ts, "samples": [sample.to_dict() for sample in scan.samples]}
        self._handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._handle.flush()

    def purge(self) -> int:
        """Alte Aufzeichnungen loeschen."""
        cutoff = time.time() - self.retention_days * 86400
        removed = 0
        for path in self.directory.glob("samples-*.ndjson*"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - Datei schon weg
                continue
        return removed

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
