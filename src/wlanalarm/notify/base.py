"""Basisklasse und Verteiler fuer Benachrichtigungen."""

from __future__ import annotations

import abc
import logging
import queue
import threading
import time

from ..alarm import LEVEL_ORDER, AlarmEvent
from ..config import NotifierConfig

log = logging.getLogger(__name__)


class NotifierError(RuntimeError):
    """Der Kanal konnte die Nachricht nicht zustellen."""


class Notifier(abc.ABC):
    """Ein Benachrichtigungskanal."""

    type_name = "base"

    def __init__(self, config: NotifierConfig) -> None:
        self.config = config
        self.name = config.name or config.type
        self.options = config.options

    @abc.abstractmethod
    def send(self, event: AlarmEvent) -> None:
        """Ein Ereignis zustellen. Fehler als :class:`NotifierError` melden."""

    def publish_state(self, state: dict) -> None:
        """Laufenden Zustand veroeffentlichen. Nur fuer Kanaele relevant, die
        einen Zustand fuehren (MQTT). Standard: nichts tun."""

    def close(self) -> None:
        """Verbindungen schliessen."""

    # -- Hilfen fuer die Implementierungen --------------------------------- #

    def option(self, key: str, default=None, required: bool = False):
        value = self.options.get(key, default)
        if required and value in (None, ""):
            raise NotifierError(
                f"Kanal '{self.name}' ({self.type_name}): Option '{key}' fehlt"
            )
        return value

    def title(self, event: AlarmEvent) -> str:
        return {
            "alarm": "WLANalarm: ALARM",
            "pending": "WLANalarm: Bewegung erkannt",
            "motion": "WLANalarm: Bewegung",
        }.get(event.type, "WLANalarm")


class NotificationHub:
    """Verteilt Ereignisse an alle Kanaele - in einem eigenen Thread.

    Ein haengender HTTP-Aufruf darf die Messschleife nicht anhalten, sonst
    reisst die Messreihe ab und der Detektor verliert seine Baseline.
    """

    def __init__(self, notifiers: list[Notifier], rate_limit_seconds: float = 60.0) -> None:
        self._notifiers = notifiers
        self._rate_limit = rate_limit_seconds
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._last_sent: dict[tuple[str, str], float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="wlanalarm-notify", daemon=True)
        self._thread.start()

    @property
    def notifiers(self) -> list[Notifier]:
        return self._notifiers

    def dispatch(self, event: AlarmEvent) -> None:
        try:
            self._queue.put_nowait(("event", event))
        except queue.Full:  # pragma: no cover - nur unter extremer Last
            log.warning("Benachrichtigungs-Warteschlange voll, Ereignis verworfen: %s", event.type)

    def publish_state(self, state: dict) -> None:
        try:
            self._queue.put_nowait(("state", state))
        except queue.Full:
            pass  # Zustandsmeldungen sind entbehrlich, das naechste kommt gleich

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                kind, payload = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if kind == "event":
                    self._deliver(payload)
                else:
                    self._publish(payload)
            except Exception:  # pragma: no cover - der Thread darf nie sterben
                log.exception("Fehler beim Verarbeiten einer Benachrichtigung")
            finally:
                self._queue.task_done()

    def _deliver(self, event: AlarmEvent) -> None:
        for notifier in self._notifiers:
            if not notifier.config.enabled:
                continue
            if LEVEL_ORDER.get(event.level, 0) < LEVEL_ORDER.get(notifier.config.min_level, 0):
                continue
            # Alarme gehen immer sofort raus, alles andere wird gedrosselt.
            if event.level != "alarm":
                key = (notifier.name, event.type)
                now = time.time()
                if now - self._last_sent.get(key, 0.0) < self._rate_limit:
                    log.debug("Kanal %s: %s wegen Drosselung uebersprungen", notifier.name, event.type)
                    continue
                self._last_sent[key] = now
            try:
                notifier.send(event)
                log.debug("Kanal %s: %s zugestellt", notifier.name, event.type)
            except Exception as exc:
                log.error("Kanal %s konnte nicht zustellen: %s", notifier.name, exc)

    def _publish(self, state: dict) -> None:
        for notifier in self._notifiers:
            if not notifier.config.enabled:
                continue
            try:
                notifier.publish_state(state)
            except Exception as exc:
                log.debug("Kanal %s: Zustand nicht veroeffentlicht: %s", notifier.name, exc)

    def close(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.05)
        self._stop.set()
        self._thread.join(timeout=2.0)
        for notifier in self._notifiers:
            try:
                notifier.close()
            except Exception:  # pragma: no cover
                pass
