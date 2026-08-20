"""Die Hauptschleife: messen, bewerten, schalten, melden.

Aufbau eines Durchlaufs:

    FRITZ!Box abfragen -> Strecken auswaehlen -> Detektor -> Alarmanlage
                                                        \\-> Aufzeichnung
                                                        \\-> Benachrichtigung
                                                        \\-> Dashboard

Alle langsamen Dinge (Benachrichtigungen, Datenbank) haengen so am Ende der
Kette, dass sie die Messtaktung nicht stoeren koennen.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

from .alarm import ARMED_STATES, STATE_DISARMED, AlarmEvent, AlarmPanel, ScheduleController
from .config import Config
from .detector import DetectionResult, MotionDetector
from .model import Scan
from .notify import NotificationHub
from .presence import PresenceTracker
from .recorder import Recorder
from .selection import Candidate, evaluate, selected_ids
from .sources.base import SampleSource, SourceError
from .storage import Storage

log = logging.getLogger(__name__)

#: Wie oft die Baselines auf die Platte geschrieben werden.
BASELINE_SAVE_INTERVAL = 300.0
#: Wie oft alte Ereignisse geloescht werden.
PURGE_INTERVAL = 3600.0
#: Laenge des Verlaufs fuer das Dashboard in Messpunkten.
HISTORY_LENGTH = 300


@dataclass
class EngineStats:
    ticks: int = 0
    errors: int = 0
    consecutive_errors: int = 0
    started: float = 0.0
    last_scan: float = 0.0
    last_error: str = ""


class Engine:
    """Fuehrt Messung, Erkennung und Alarmanlage zusammen."""

    def __init__(
        self,
        config: Config,
        source: SampleSource,
        storage: Storage | None = None,
        hub: NotificationHub | None = None,
        recorder: Recorder | None = None,
        presence: PresenceTracker | None = None,
        clock=time.time,
    ) -> None:
        self._config = config
        self._source = source
        self._storage = storage
        self._hub = hub
        self._recorder = recorder
        self._presence = presence
        self._clock = clock

        self._detector = MotionDetector(config)
        self._panel = AlarmPanel(config.alarm, clock())
        self._schedule = ScheduleController(config.alarm.schedule)
        self._lock = threading.RLock()

        self._candidates: list[Candidate] = []
        self._selected: set[str] = set()
        self._last_result: DetectionResult | None = None
        self._history: deque[tuple[float, float, bool]] = deque(maxlen=HISTORY_LENGTH)
        self.stats = EngineStats(started=clock())
        self._last_baseline_save = clock()
        self._last_purge = clock()

        self._restore()
        self._wire_commands()

    # -- Start ------------------------------------------------------------- #

    def _restore(self) -> None:
        """Kalibrierte Baselines und den letzten Modus wiederherstellen."""
        if self._storage is None:
            return
        snapshots = self._storage.load_baselines()
        if snapshots:
            self._detector.seed_baselines(snapshots)
            log.info("%d gespeicherte Baselines geladen", len(snapshots))
        saved_mode = self._storage.get_state("mode")
        if saved_mode in ARMED_STATES and self._config.alarm.initial_mode == STATE_DISARMED:
            log.info("Letzter Modus %s wird wiederhergestellt", saved_mode)
            self._emit(self._panel.arm(saved_mode, self._clock(), instant=True, source="restore"))

    def _wire_commands(self) -> None:
        """MQTT-Schaltbefehle mit der Anlage verbinden."""
        if self._hub is None:
            return
        for notifier in self._hub.notifiers:
            if hasattr(notifier, "command_handler"):
                notifier.command_handler = self.set_mode

    # -- Steuerung (thread-sicher) ----------------------------------------- #

    def set_mode(self, mode: str, source: str = "manual") -> dict:
        """Modus setzen. ``mode`` ist 'disarmed' oder ein armed_*-Modus."""
        with self._lock:
            now = self._clock()
            if mode == STATE_DISARMED:
                events = self._panel.disarm(now, source=source)
            elif mode in ARMED_STATES:
                events = self._panel.arm(mode, now, source=source)
            else:
                raise ValueError(f"Unbekannter Modus: {mode!r}")
            self._emit(events)
            if self._storage is not None:
                self._storage.set_state("mode", self._panel.mode)
            return self._panel.status(now)

    # -- Ein Messzyklus ---------------------------------------------------- #

    def tick(self) -> DetectionResult | None:
        """Einen Durchlauf ausfuehren. Gibt ``None`` bei Messfehler zurueck."""
        try:
            scan = self._source.scan()
        except SourceError as exc:
            with self._lock:
                self.stats.errors += 1
                self.stats.consecutive_errors += 1
                self.stats.last_error = str(exc)
            level = logging.ERROR if self.stats.consecutive_errors in (1, 10) else logging.DEBUG
            log.log(level, "Messung fehlgeschlagen (%d in Folge): %s",
                    self.stats.consecutive_errors, exc)
            return None

        with self._lock:
            self.stats.consecutive_errors = 0
            self.stats.ticks += 1
            self.stats.last_scan = scan.ts
            return self._process(scan)

    def _process(self, scan: Scan) -> DetectionResult:
        candidates = evaluate(scan, self._config)
        chosen = selected_ids(candidates)

        if self._recorder is not None:
            # Nur die tatsaechlich verwendeten Sensorstrecken aufzeichnen.
            # Der ungefilterte Scan enthaelt auch die Geraete, die die Auswahl
            # gerade aussortiert hat - Handys und Gastgeraete. Ein Mitschnitt
            # davon waere ein Sekundenprotokoll darueber, wer wann zu Hause
            # war, und damit genau die Datenart, vor deren heimlicher Erhebung
            # docs/grenzen-und-datenschutz.md warnt.
            try:
                self._recorder.write(
                    Scan(ts=scan.ts,
                         samples=[s for s in scan.samples if s.link_id in chosen])
                )
            except OSError as exc:
                log.warning("Aufzeichnung fehlgeschlagen: %s", exc)

        if chosen != self._selected:
            added = chosen - self._selected
            removed = self._selected - chosen
            if self._selected:
                for link_id in added:
                    log.info("Neue Sensorstrecke: %s", link_id)
                for link_id in removed:
                    log.info("Sensorstrecke entfallen: %s", link_id)
            else:
                log.info("%d Sensorstrecken ausgewaehlt", len(chosen))
            self._selected = chosen
        self._candidates = candidates

        result = self._detector.update(scan, chosen)
        self._last_result = result
        self._history.append((result.ts, result.score, result.motion))

        now = scan.ts
        self._apply_presence(now)
        self._apply_schedule(now)
        self._emit(self._panel.update(now, result))
        self._housekeeping(now)

        if self._hub is not None:
            self._hub.publish_state(self.snapshot(brief=True))
        return result

    # -- Automatik --------------------------------------------------------- #

    def _apply_presence(self, now: float) -> None:
        if self._presence is None:
            return
        changed = self._presence.poll(now)
        if changed is None:
            return
        config = self._config.alarm.presence
        target = config.home_mode if changed else config.away_mode
        if target == self._panel.mode:
            return
        log.info("Anwesenheit geaendert -> Modus %s", target)
        if target == STATE_DISARMED:
            self._emit(self._panel.disarm(now, source="presence"))
        else:
            # Wer nach Hause kommt, soll nicht durch die eigene Ausgangs-
            # verzoegerung stolpern - beim Weggehen ist sie dagegen richtig.
            self._emit(self._panel.arm(target, now, source="presence"))
        if self._storage is not None:
            self._storage.set_state("mode", self._panel.mode)

    def _apply_schedule(self, now: float) -> None:
        due = self._schedule.due(now)
        if due is None:
            return
        action, reason = due
        log.info("%s -> Modus %s", reason, action)
        if action == STATE_DISARMED:
            self._emit(self._panel.disarm(now, source=reason))
        else:
            self._emit(self._panel.arm(action, now, instant=True, source=reason))
        if self._storage is not None:
            self._storage.set_state("mode", self._panel.mode)

    def _housekeeping(self, now: float) -> None:
        if self._storage is None:
            return
        if now - self._last_baseline_save >= BASELINE_SAVE_INTERVAL:
            self._last_baseline_save = now
            snapshots = self._detector.export_baselines()
            if snapshots:
                self._storage.save_baselines(snapshots)
                log.debug("%d Baselines gespeichert", len(snapshots))
        if now - self._last_purge >= PURGE_INTERVAL:
            self._last_purge = now
            removed = self._storage.purge_events(self._config.storage.event_retention_days)
            if removed:
                log.info("%d alte Ereignisse geloescht", removed)
            if self._recorder is not None:
                self._recorder.purge()

    def _emit(self, events: list[AlarmEvent]) -> None:
        for event in events:
            log.debug("Ereignis: %s - %s", event.type, event.message)
            if self._storage is not None:
                self._storage.add_event(event)
            if self._hub is not None:
                if event.level == "motion" and event.type == "motion":
                    if not (
                        self._config.alarm.notify_motion_when_disarmed
                        or self._panel.is_armed
                    ):
                        continue
                self._hub.dispatch(event)

    # -- Zustand fuer Dashboard und MQTT ----------------------------------- #

    def snapshot(self, brief: bool = False) -> dict:
        with self._lock:
            now = self._clock()
            result = self._last_result
            data = {
                **self._panel.status(now),
                "score": round(result.score, 3) if result else 0.0,
                "ready": result.ready if result else False,
                "ticks": self.stats.ticks,
                "errors": self.stats.errors,
                "consecutive_errors": self.stats.consecutive_errors,
                "last_error": self.stats.last_error,
                "last_scan": self.stats.last_scan,
                "last_scan_age": (
                    round(now - self.stats.last_scan, 1) if self.stats.last_scan else None
                ),
                "uptime": round(now - self.stats.started, 1),
                "source": self._source.name,
                "link_count": len(self._selected),
                # Die geltenden Schwellen mitschicken, damit das Dashboard die
                # angezeigten Zahlen einordnen kann statt sie nur auszugeben.
                "trigger_score": self._config.detector.trigger_score,
                "min_links": self._config.detector.min_links,
                "warmup_samples": self._config.sampling.warmup_samples,
                # Zonen und ausloesende Geraete gehoeren in den Zustand, nicht
                # nur in die Ereignisse: In Home Assistant sind sie die
                # Attribute des Bewegungsmelders, ueber die Automatisierungen
                # entscheiden ("nur wenn im Flur").
                "zones": result.zones if result else [],
                "triggered": (
                    [link.peer_name or link.peer_mac for link in result.triggered_links]
                    if result else []
                ),
            }
            if brief:
                return data
            data["links"] = [link.to_dict() for link in (result.links if result else [])]
            data["candidates"] = [c.to_dict() for c in self._candidates]
            data["history"] = [
                {"ts": ts, "score": round(score, 3), "motion": motion}
                for ts, score, motion in self._history
            ]
            return data

    @property
    def panel(self) -> AlarmPanel:
        return self._panel

    @property
    def detector(self) -> MotionDetector:
        return self._detector

    @property
    def candidates(self) -> list[Candidate]:
        return self._candidates

    # -- Dauerbetrieb ------------------------------------------------------ #

    def run(self, stop_event: threading.Event | None = None, max_ticks: int | None = None) -> None:
        """Endlosschleife mit stabilem Takt."""
        stop_event = stop_event or threading.Event()
        interval = self._config.sampling.interval
        next_tick = time.monotonic()
        count = 0
        log.info(
            "WLANalarm laeuft (Quelle: %s, Takt: %.1f s, Modus: %s)",
            self._source.name,
            interval,
            self._panel.mode,
        )
        while not stop_event.is_set():
            self.tick()
            count += 1
            if max_ticks is not None and count >= max_ticks:
                break
            # Auf den naechsten Takt warten. Bei Verzug wird uebersprungen
            # statt aufzuholen, sonst laeuft die Schleife nach einer langen
            # Stoerung im Eiltempo durch.
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay < 0:
                next_tick = time.monotonic()
                delay = 0
            if stop_event.wait(delay):
                break
        self.shutdown()

    def shutdown(self) -> None:
        log.info("WLANalarm wird beendet")
        if self._storage is not None:
            snapshots = self._detector.export_baselines()
            if snapshots:
                self._storage.save_baselines(snapshots)
            self._storage.set_state("mode", self._panel.mode)
        if self._recorder is not None:
            self._recorder.close()
        if self._hub is not None:
            self._hub.close()
        try:
            self._source.close()
        except Exception:  # pragma: no cover
            pass
