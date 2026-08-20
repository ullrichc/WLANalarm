"""Robuste Statistik und laufende Ruhe-Baseline je Funkstrecke.

Warum robust und nicht einfach Mittelwert/Standardabweichung: eine einzelne
Ausreissermessung - die FRITZ!Box liefert gelegentlich einen voellig
danebenliegenden RSSI, wenn ein Client gerade den Kanal wechselt - wuerde
Mittelwert und Standardabweichung massiv verzerren und danach fuer Minuten
jede Bewegung unter der Schwelle verschwinden lassen. Median und MAD
interessieren sich fuer solche Einzelwerte nicht.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

#: Umrechnungsfaktor MAD -> Standardabweichung bei normalverteilten Daten.
MAD_TO_SIGMA = 1.4826


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def mad(values: list[float], centre: float | None = None) -> float:
    """Median der absoluten Abweichungen vom Median."""
    if not values:
        return 0.0
    mid = statistics.median(values) if centre is None else centre
    return statistics.median([abs(value - mid) for value in values])


def robust_scale(values: list[float], centre: float | None = None) -> float:
    """Streuungsmass, das gegen Ausreisser unempfindlich ist."""
    return mad(values, centre) * MAD_TO_SIGMA


def stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def mean_abs_diff(values: list[float]) -> float:
    """Mittlere Aenderung von Messpunkt zu Messpunkt.

    Trennt stark schwankende Signale (Bewegung) von langsam driftenden (Temperatur,
    Kanalwechsel) - beide koennen dieselbe Standardabweichung haben.
    """
    if len(values) < 2:
        return 0.0
    diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return sum(diffs) / len(diffs)


@dataclass
class BaselineSnapshot:
    """Gespeicherter Ruhezustand einer Funkstrecke."""

    median: float
    scale: float
    samples: int
    updated: float = 0.0

    def to_dict(self) -> dict:
        return {
            "median": round(self.median, 4),
            "scale": round(self.scale, 4),
            "samples": self.samples,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BaselineSnapshot":
        return cls(
            median=float(data.get("median", 0.0)),
            scale=float(data.get("scale", 0.0)),
            samples=int(data.get("samples", 0)),
            updated=float(data.get("updated", 0.0)),
        )


class RollingBaseline:
    """Laufender Median/MAD der Aktivitaetskennzahl in Ruhephasen.

    Es werden ausschliesslich Werte aufgenommen, die der Detektor als ruhig
    einstuft. Sonst wuerde die Baseline waehrend einer laengeren Anwesenheit
    die Bewegung als Normalzustand lernen und danach nichts mehr melden.
    """

    def __init__(self, window_seconds: float, min_samples: int = 30) -> None:
        self._window = window_seconds
        self._min_samples = min_samples
        self._values: deque[tuple[float, float]] = deque()
        self._seed: BaselineSnapshot | None = None

    def seed(self, snapshot: BaselineSnapshot) -> None:
        """Vorberechnete Baseline (aus der Kalibrierung) uebernehmen."""
        self._seed = snapshot

    def add(self, ts: float, value: float) -> None:
        self._values.append((ts, value))
        cutoff = ts - self._window
        while self._values and self._values[0][0] < cutoff:
            self._values.popleft()

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def ready(self) -> bool:
        return self.count >= self._min_samples or self._seed is not None

    def snapshot(self) -> BaselineSnapshot:
        """Aktuelle Baseline; faellt auf die Kalibrierung zurueck, solange
        noch zu wenige eigene Ruhewerte vorliegen."""
        if self.count < self._min_samples and self._seed is not None:
            return self._seed
        values = [value for _, value in self._values]
        centre = median(values)
        return BaselineSnapshot(
            median=centre,
            scale=robust_scale(values, centre),
            samples=len(values),
        )
