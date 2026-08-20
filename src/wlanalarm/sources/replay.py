"""Aufgezeichnete Messwerte erneut abspielen.

Damit lassen sich Schwellen offline nachjustieren: einmal mit
``wlanalarm record`` eine Nacht mitschneiden, danach beliebig oft
``wlanalarm replay`` mit veraenderten Parametern laufen lassen.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterator

from ..model import LinkSample, Scan
from .base import SampleSource, SourceError


def iter_recording(path: str | Path) -> Iterator[Scan]:
    """Eine Aufzeichnung (NDJSON, optional gzip-komprimiert) einlesen.

    Die Existenzpruefung passiert hier und nicht im Generator, damit ein
    falscher Pfad sofort auffaellt und nicht erst beim ersten Lesezugriff.
    """
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise SourceError(f"Aufzeichnung nicht gefunden: {file_path}")
    return _iter_scans(file_path)


def _iter_scans(file_path: Path) -> Iterator[Scan]:
    opener = gzip.open if file_path.suffix == ".gz" else open
    with opener(file_path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                yield Scan(
                    ts=float(record["ts"]),
                    samples=[LinkSample.from_dict(item) for item in record.get("samples", [])],
                )
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError(f"{file_path}:{line_number}: fehlerhafte Zeile: {exc}") from exc


class ReplaySource(SampleSource):
    """Spielt eine Aufzeichnung als Datenquelle ab."""

    name = "replay"

    def __init__(self, path: str | Path) -> None:
        self._path = path
        self._iterator = iter_recording(path)

    def scan(self) -> Scan:
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise SourceError(f"Aufzeichnung {self._path} ist zu Ende") from exc
