"""Bewegungserkennung aus den Messreihen der Funkstrecken.

Verfahren je Funkstrecke:

1. Aus dem kurzen Analysefenster wird eine *Aktivitaetskennzahl* in dB
   gebildet - im Kern Streuung und Kurzzeitschwankung des RSSI, ergaenzt um die
   Schwankung der ausgehandelten Datenrate.
2. Diese Kennzahl wird robust (Median/MAD) gegen das Ruheverhalten genau
   dieser Strecke normiert. Jede Strecke bekommt also ihre eigene Schwelle -
   eine Strecke zu einem Repeater im Nebenzimmer rauscht ganz anders als die
   zu einem Lautsprecher zwei Meter neben der Box.
3. Aus dem z-Wert wird ein Score zwischen 0 und 1.

Ueber alle Strecken hinweg gilt Bewegung erst dann als erkannt, wenn entweder
mehrere Strecken gleichzeitig ausschlagen oder eine einzelne sehr deutlich.
Das ist der wirksamste Hebel gegen Fehlalarme: ein einzelnes auffaelliges Geraet
(Firmware-Update, Stromsparmodus, Kanalwechsel) reisst die Anlage nicht hoch,
ein Mensch im Raum stoert dagegen mehrere Strecken zugleich.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from .baseline import BaselineSnapshot, RollingBaseline, mean_abs_diff, stdev
from .config import Config, DetectorConfig, SamplingConfig
from .model import LinkSample, Scan

log = logging.getLogger(__name__)

#: Naeherung fuer den TR-064-Fallback: die FRITZ!Box bildet rund -100..-30 dBm
#: auf 0..100 % ab, ein Prozentpunkt entspricht damit etwa 0,7 dB.
PERCENT_TO_DB = 0.7


@dataclass
class LinkResult:
    """Bewertung einer Funkstrecke zu einem Zeitpunkt."""

    link_id: str
    label: str
    peer_mac: str
    peer_name: str
    zone: str
    band: str
    weight: float
    #: ``None`` bedeutet "noch nicht berechenbar" - im Unterschied zu 0.0,
    #: was ein vollkommen stabiles Signal beschreibt.
    activity_db: float | None
    baseline_db: float
    baseline_scale_db: float
    z: float
    score: float
    triggered: bool
    ready: bool
    window_samples: int
    rssi_dbm: float | None
    #: Strecke ist als Sensor ungeeignet (Ruheverhalten zu unruhig).
    unstable: bool = False

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "label": self.label,
            "peer_mac": self.peer_mac,
            "peer_name": self.peer_name,
            "zone": self.zone,
            "band": self.band,
            "weight": self.weight,
            "activity_db": None if self.activity_db is None else round(self.activity_db, 3),
            "baseline_db": round(self.baseline_db, 3),
            "baseline_scale_db": round(self.baseline_scale_db, 3),
            "z": round(self.z, 2),
            "score": round(self.score, 3),
            "triggered": self.triggered,
            "ready": self.ready,
            "window_samples": self.window_samples,
            "rssi_dbm": self.rssi_dbm,
            "unstable": self.unstable,
        }


@dataclass
class DetectionResult:
    """Gesamtergebnis eines Messzyklus."""

    ts: float
    motion: bool
    score: float
    ready: bool
    links: list[LinkResult] = field(default_factory=list)
    triggered_links: list[LinkResult] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.triggered_links:
            return "keine Bewegung"
        names = ", ".join(link.peer_name or link.peer_mac for link in self.triggered_links)
        zones = ", ".join(self.zones) if self.zones else "ohne Zone"
        return f"Bewegung (Score {self.score:.2f}) an: {names} [Zone: {zones}]"

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "motion": self.motion,
            "score": round(self.score, 3),
            "ready": self.ready,
            "zones": self.zones,
            "links": [link.to_dict() for link in self.links],
        }


class LinkTracker:
    """Messreihe und Baseline einer einzelnen Funkstrecke."""

    def __init__(self, sample: LinkSample, sampling: SamplingConfig) -> None:
        self.link_id = sample.link_id
        self.sample = sample
        self.last_seen = sample.ts
        self._sampling = sampling
        capacity = max(8, int(sampling.window_seconds / sampling.interval) + 4)
        #: (Zeit, Signal in dB, Datenrate, Stoerabstand in dB)
        self._window: deque[tuple[float, float, float | None, float | None]] = deque(
            maxlen=capacity
        )
        self.baseline = RollingBaseline(
            window_seconds=sampling.baseline_seconds,
            min_samples=max(10, int(sampling.baseline_seconds / sampling.interval / 6)),
        )
        self.consecutive_triggers = 0

    def add(self, sample: LinkSample) -> None:
        self.sample = sample
        self.last_seen = sample.ts
        signal = _signal_db(sample)
        if signal is None:
            return
        self._window.append((sample.ts, signal, sample.rx_rate_kbps, sample.snr_db))
        cutoff = sample.ts - self._sampling.window_seconds
        while len(self._window) > 3 and self._window[0][0] < cutoff:
            self._window.popleft()

    @property
    def window_samples(self) -> int:
        return len(self._window)

    def activity(self, config: DetectorConfig) -> float | None:
        """Aktivitaetskennzahl des aktuellen Fensters in dB."""
        if len(self._window) < 4:
            return None
        signals = [signal for _, signal, _, _ in self._window]
        rates = [rate for _, _, rate, _ in self._window if rate is not None]
        snrs = [snr for _, _, _, snr in self._window if snr is not None]

        value = (
            config.weight_std * stdev(signals)
            + config.weight_jitter * mean_abs_diff(signals)
            + config.weight_range * (max(signals) - min(signals))
        )
        if len(rates) >= 4:
            average = sum(rates) / len(rates)
            if average > 0:
                # Relative Streuung der Datenrate, auf eine dB-aehnliche
                # Groessenordnung skaliert.
                value += config.weight_rate * 10.0 * (stdev(rates) / average)
        if len(snrs) >= 4:
            # Streuung und Kurzzeitschwankung des Stoerabstands, gemittelt, damit der
            # Beitrag mit dem des Signals vergleichbar bleibt.
            value += config.weight_snr * (stdev(snrs) + mean_abs_diff(snrs)) / 2
        return value


class MotionDetector:
    """Fuehrt die Tracker aller Funkstrecken und faellt das Gesamturteil."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._detector = config.detector
        self._sampling = config.sampling
        self._trackers: dict[str, LinkTracker] = {}
        self._pending_seeds: dict[str, BaselineSnapshot] = {}
        self._motion_active = False
        self._last_motion_ts = 0.0
        self._total_samples = 0
        self._consecutive = 0

    # -- Baseline-Persistenz ---------------------------------------------- #

    def seed_baselines(self, snapshots: dict[str, BaselineSnapshot]) -> int:
        """Kalibrierte Baselines uebernehmen. Gibt die Zahl der Treffer zurueck."""
        self._pending_seeds = dict(snapshots)
        applied = 0
        for link_id, snapshot in snapshots.items():
            tracker = self._trackers.get(link_id)
            if tracker is not None:
                tracker.baseline.seed(snapshot)
                applied += 1
        return applied

    def export_baselines(self) -> dict[str, BaselineSnapshot]:
        return {
            link_id: tracker.baseline.snapshot()
            for link_id, tracker in self._trackers.items()
            if tracker.baseline.ready
        }

    # -- Hauptschleife ----------------------------------------------------- #

    def update(self, scan: Scan, selected: set[str] | None = None) -> DetectionResult:
        """Einen Messzyklus verarbeiten.

        Args:
            scan: die frischen Messwerte.
            selected: nur diese Link-IDs bewerten; ``None`` = alle.
        """
        self._total_samples += 1
        self._expire(scan.ts)

        results: list[LinkResult] = []
        for sample in scan.samples:
            if selected is not None and sample.link_id not in selected:
                continue
            if not sample.has_signal:
                continue
            tracker = self._trackers.get(sample.link_id)
            if tracker is None:
                tracker = LinkTracker(sample, self._sampling)
                seed = self._pending_seeds.get(sample.link_id)
                if seed is not None:
                    tracker.baseline.seed(seed)
                self._trackers[sample.link_id] = tracker
            tracker.add(sample)
            result = self._evaluate(tracker)
            if result is not None:
                results.append(result)

        ready = self._total_samples >= self._sampling.warmup_samples and any(
            r.ready for r in results
        )
        triggered = [r for r in results if r.triggered]
        score = max((r.score for r in results if r.weight > 0), default=0.0)

        motion = self._decide(scan.ts, triggered, results, ready)
        # Baselines nur in Ruhephasen fortschreiben.
        self._update_baselines(results, motion)

        zones = sorted({r.zone for r in triggered}) if motion else []
        return DetectionResult(
            ts=scan.ts,
            motion=motion,
            score=score,
            ready=ready,
            links=sorted(results, key=lambda r: r.score, reverse=True),
            triggered_links=triggered if motion else [],
            zones=zones,
        )

    # -- Details ----------------------------------------------------------- #

    def _evaluate(self, tracker: LinkTracker) -> LinkResult | None:
        sample = tracker.sample
        link_config = self._config.link_config(sample.peer_mac)
        zone = link_config.zone if link_config else "default"
        weight = link_config.weight if link_config else 1.0
        name = (link_config.name if link_config and link_config.name else sample.peer_name)

        activity = tracker.activity(self._detector)
        baseline = tracker.baseline.snapshot()
        ready = tracker.baseline.ready and activity is not None

        if activity is None:
            return LinkResult(
                link_id=tracker.link_id,
                label=sample.label,
                peer_mac=sample.peer_mac,
                peer_name=name,
                zone=zone,
                band=sample.band,
                weight=weight,
                activity_db=None,
                baseline_db=baseline.median,
                baseline_scale_db=baseline.scale,
                z=0.0,
                score=0.0,
                triggered=False,
                ready=False,
                window_samples=tracker.window_samples,
                rssi_dbm=sample.rssi_dbm,
            )

        scale = max(baseline.scale, self._detector.min_baseline_scale_db)
        delta = activity - baseline.median
        z = delta / scale
        unstable = baseline.median > self._detector.max_baseline_activity_db

        if not ready or delta < self._detector.min_delta_db or unstable or weight <= 0:
            score = 0.0
        else:
            score = max(0.0, min(1.0, z / self._detector.z_full_scale))

        threshold = (
            link_config.trigger_score
            if link_config and link_config.trigger_score is not None
            else self._detector.trigger_score
        )
        # Hysterese: eine bereits ausgeloeste Strecke bleibt es, bis sie unter
        # clear_score faellt. Sonst flackert der Zustand am Schwellwert.
        was_triggered = tracker.consecutive_triggers > 0
        active_threshold = self._detector.clear_score if was_triggered else threshold
        triggered = score >= active_threshold
        tracker.consecutive_triggers = tracker.consecutive_triggers + 1 if triggered else 0

        return LinkResult(
            link_id=tracker.link_id,
            label=sample.label,
            peer_mac=sample.peer_mac,
            peer_name=name,
            zone=zone,
            band=sample.band,
            weight=weight,
            activity_db=activity,
            baseline_db=baseline.median,
            baseline_scale_db=baseline.scale,
            z=z,
            score=score,
            triggered=triggered,
            ready=ready,
            window_samples=tracker.window_samples,
            rssi_dbm=sample.rssi_dbm,
            unstable=unstable,
        )

    def _decide(
        self,
        ts: float,
        triggered: list[LinkResult],
        results: list[LinkResult],
        ready: bool,
    ) -> bool:
        """Gesamturteil mit Mehrheits- und Haltebedingung."""
        if not ready:
            return False
        config = self._detector
        enough = len(triggered) >= config.min_links
        # Ein einzelner sehr starker Ausschlag zaehlt nur, wenn er nicht ohnehin
        # von ruhigen Nachbarstrecken widerlegt wird. Stehen weniger Strecken
        # bereit als min_links verlangt, waere die Bedingung sonst nie erfuellbar.
        usable = sum(1 for link in results if link.ready and link.weight > 0)
        single_allowed = config.allow_single_strong or usable < config.min_links
        strong = single_allowed and any(
            link.score >= config.strong_score for link in triggered
        )
        raw_motion = strong or enough

        if raw_motion:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if self._motion_active:
            if raw_motion:
                self._last_motion_ts = ts
                return True
            # Nachlaufzeit, damit kurze Pausen beim Gehen den Zustand nicht
            # sofort zuruecksetzen.
            if ts - self._last_motion_ts < config.clear_seconds:
                return True
            self._motion_active = False
            return False

        if raw_motion and self._consecutive >= config.trigger_consecutive:
            self._motion_active = True
            self._last_motion_ts = ts
            return True
        return False

    def _update_baselines(self, results: list[LinkResult], motion: bool) -> None:
        for result in results:
            tracker = self._trackers.get(result.link_id)
            if tracker is None or result.activity_db is None:
                continue
            # Bei erkannter Bewegung oder ausgeloester Strecke nichts lernen.
            if motion or result.triggered:
                continue
            tracker.baseline.add(tracker.sample.ts, result.activity_db)

    def _expire(self, ts: float) -> None:
        limit = self._detector.stale_after_seconds
        stale = [
            link_id
            for link_id, tracker in self._trackers.items()
            if ts - tracker.last_seen > limit
        ]
        for link_id in stale:
            log.info("Funkstrecke %s ist verschwunden, Zustand verworfen", link_id)
            del self._trackers[link_id]

    @property
    def trackers(self) -> dict[str, LinkTracker]:
        return self._trackers


def _signal_db(sample: LinkSample) -> float | None:
    """Messgroesse in dB, nach Guete der Quelle geordnet.

    Erste Wahl ist die Empfangsleistung in dBm. Fehlt sie, taugt der
    Stoerabstand ebenso als Traegergroesse - er schwankt mit derselben
    Ursache. Die Prozentangabe aus TR-064 ist die letzte Rueckfallebene und
    so grob quantisiert, dass feine Aenderungen darin untergehen.
    """
    if sample.rssi_dbm is not None:
        return sample.rssi_dbm
    if sample.snr_db is not None:
        return sample.snr_db
    if sample.signal_percent is not None:
        return -100.0 + sample.signal_percent * PERCENT_TO_DB
    return None
