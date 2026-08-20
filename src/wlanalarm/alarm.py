"""Alarmanlage: Zustandsautomat, Zonen, Verzoegerungen, Zeitplan.

Zustaende:

    disarmed          unscharf, es wird nur gemessen
    arming            Ausgangsverzoegerung laeuft (Wohnung verlassen)
    armed_home/away/night   scharf
    pending           Bewegung erkannt, Eingangsverzoegerung laeuft
    alarm             ausgeloest, bleibt bis zum Entschaerfen bestehen

Bewegung waehrend der Ausgangsverzoegerung wird bewusst ignoriert - sonst
loest die eigene Bewegung beim Verlassen der Wohnung den Alarm aus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime

from .config import AlarmConfig, ScheduleConfig
from .detector import DetectionResult

log = logging.getLogger(__name__)

STATE_DISARMED = "disarmed"
STATE_ARMING = "arming"
STATE_PENDING = "pending"
STATE_ALARM = "alarm"

ARMED_STATES = ("armed_home", "armed_away", "armed_night")

#: Rangfolge fuer die Filterung in den Benachrichtigungskanaelen.
LEVEL_ORDER = {"motion": 0, "armed_motion": 1, "alarm": 2}


@dataclass
class AlarmEvent:
    """Ein meldenswertes Ereignis."""

    ts: float
    type: str
    level: str
    message: str
    mode: str = STATE_DISARMED
    zones: list[str] = field(default_factory=list)
    score: float = 0.0
    links: list[str] = field(default_factory=list)
    source: str = "system"

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "type": self.type,
            "level": self.level,
            "message": self.message,
            "mode": self.mode,
            "zones": self.zones,
            "score": round(self.score, 3),
            "links": self.links,
            "source": self.source,
        }


class AlarmPanel:
    """Der Zustandsautomat der Anlage."""

    def __init__(self, config: AlarmConfig, now: float) -> None:
        self._config = config
        self._state = STATE_DISARMED
        #: Zielmodus - waehrend arming/pending/alarm der Modus, in den
        #: geschaltet wurde bzw. der ausgeloest hat.
        self._mode = STATE_DISARMED
        self._deadline = 0.0
        self._cooldown_until = 0.0
        self._motion = False
        self._last_change = now
        self._armed_by = "start"
        if config.initial_mode in ARMED_STATES:
            self.arm(config.initial_mode, now, instant=True, source="start")

    # -- Zustand ----------------------------------------------------------- #

    @property
    def state(self) -> str:
        return self._state

    @property
    def mode(self) -> str:
        """Der eingestellte Modus, auch waehrend pending/alarm."""
        return self._mode

    @property
    def is_armed(self) -> bool:
        return self._mode in ARMED_STATES

    @property
    def motion(self) -> bool:
        return self._motion

    @property
    def deadline(self) -> float:
        return self._deadline

    def status(self, now: float) -> dict:
        remaining = max(0.0, self._deadline - now) if self._deadline else 0.0
        return {
            "state": self._state,
            "mode": self._mode,
            "motion": self._motion,
            "remaining": round(remaining, 1),
            "armed_by": self._armed_by,
            "since": self._last_change,
            "cooldown": round(max(0.0, self._cooldown_until - now), 1),
        }

    # -- Steuerung --------------------------------------------------------- #

    def arm(
        self,
        mode: str,
        now: float,
        *,
        instant: bool = False,
        source: str = "manual",
    ) -> list[AlarmEvent]:
        """Anlage scharf schalten."""
        if mode not in ARMED_STATES:
            raise ValueError(f"Unbekannter Modus: {mode!r}")
        if self._mode == mode and self._state not in (STATE_DISARMED,):
            return []
        self._mode = mode
        self._armed_by = source
        self._last_change = now
        delay = 0.0 if instant else self._config.exit_delay
        if delay > 0:
            self._state = STATE_ARMING
            self._deadline = now + delay
            return [
                self._event(
                    now,
                    "arming",
                    "motion",
                    f"Anlage wird in {int(delay)} s scharf ({_mode_label(mode)})",
                    source=source,
                )
            ]
        self._state = mode
        self._deadline = 0.0
        return [
            self._event(now, "armed", "motion", f"Anlage scharf ({_mode_label(mode)})", source=source)
        ]

    def disarm(self, now: float, source: str = "manual") -> list[AlarmEvent]:
        """Anlage entschaerfen."""
        if self._state == STATE_DISARMED and self._mode == STATE_DISARMED:
            return []
        was_alarm = self._state == STATE_ALARM
        self._state = STATE_DISARMED
        self._mode = STATE_DISARMED
        self._deadline = 0.0
        self._armed_by = source
        self._last_change = now
        if was_alarm:
            # Nach einem Alarm eine Weile nicht erneut ausloesen, damit das
            # Aufraeumen danach keine Alarmkette produziert.
            self._cooldown_until = now + self._config.cooldown
        return [self._event(now, "disarmed", "motion", "Anlage unscharf", source=source)]

    # -- Messzyklus -------------------------------------------------------- #

    def update(self, now: float, detection: DetectionResult) -> list[AlarmEvent]:
        """Detektorergebnis verarbeiten und Ereignisse zurueckgeben."""
        events: list[AlarmEvent] = []

        # Ausgangsverzoegerung abgelaufen?
        if self._state == STATE_ARMING and now >= self._deadline:
            self._state = self._mode
            self._deadline = 0.0
            events.append(
                self._event(
                    now,
                    "armed",
                    "motion",
                    f"Anlage scharf ({_mode_label(self._mode)})",
                    source=self._armed_by,
                )
            )

        relevant = self._relevant_motion(detection)

        # Reine Bewegungsmeldung - unabhaengig vom Modus.
        if detection.motion and not self._motion:
            self._motion = True
            events.append(
                self._event(
                    now,
                    "motion",
                    "armed_motion" if self._state in ARMED_STATES else "motion",
                    detection.summary,
                    zones=detection.zones,
                    score=detection.score,
                    links=[link.peer_name or link.peer_mac for link in detection.triggered_links],
                )
            )
        elif not detection.motion and self._motion:
            self._motion = False
            events.append(self._event(now, "motion_cleared", "motion", "Ruhe"))

        # Eingangsverzoegerung starten.
        if self._state in ARMED_STATES and relevant:
            if now < self._cooldown_until:
                log.debug("Bewegung waehrend der Sperrzeit nach einem Alarm ignoriert")
            elif self._config.entry_delay > 0:
                self._state = STATE_PENDING
                self._deadline = now + self._config.entry_delay
                events.append(
                    self._event(
                        now,
                        "pending",
                        "armed_motion",
                        f"Bewegung erkannt - Alarm in {int(self._config.entry_delay)} s",
                        zones=detection.zones,
                        score=detection.score,
                        links=[
                            link.peer_name or link.peer_mac
                            for link in detection.triggered_links
                        ],
                    )
                )
            else:
                events.append(self._raise_alarm(now, detection))

        # Eingangsverzoegerung abgelaufen -> Alarm.
        elif self._state == STATE_PENDING and now >= self._deadline:
            events.append(self._raise_alarm(now, detection))

        return events

    # -- intern ------------------------------------------------------------ #

    def _relevant_motion(self, detection: DetectionResult) -> bool:
        """Bewegung in einer Zone, die im aktuellen Modus ueberwacht wird?"""
        if not detection.motion:
            return False
        zones = self._config.zones_for_mode(self._mode)
        if not zones:
            return True
        return any(zone in zones for zone in detection.zones)

    def _raise_alarm(self, now: float, detection: DetectionResult) -> AlarmEvent:
        self._state = STATE_ALARM
        self._deadline = 0.0
        self._last_change = now
        names = ", ".join(
            link.peer_name or link.peer_mac for link in detection.triggered_links
        )
        where = f" an: {names}" if names else ""
        return self._event(
            now,
            "alarm",
            "alarm",
            f"ALARM - Bewegung im Modus {_mode_label(self._mode)}{where}",
            zones=detection.zones,
            score=detection.score,
            links=[link.peer_name or link.peer_mac for link in detection.triggered_links],
        )

    def _event(
        self,
        ts: float,
        type_: str,
        level: str,
        message: str,
        *,
        zones: list[str] | None = None,
        score: float = 0.0,
        links: list[str] | None = None,
        source: str = "system",
    ) -> AlarmEvent:
        return AlarmEvent(
            ts=ts,
            type=type_,
            level=level,
            message=message,
            mode=self._mode,
            zones=zones or [],
            score=score,
            links=links or [],
            source=source,
        )


class ScheduleController:
    """Zeitgesteuertes Scharfschalten ('Nachtwache').

    Prueft bei jedem Tick, ob eine Schaltzeit ueberschritten wurde. Es wird
    bewusst auf einen Flankenwechsel geprueft und nicht auf ein Zeitfenster:
    so kann man zwischendurch von Hand entschaerfen, ohne dass der Zeitplan
    eine Sekunde spaeter wieder scharf schaltet.
    """

    def __init__(self, config: ScheduleConfig) -> None:
        self._config = config
        self._last_check: datetime | None = None

    def due(self, now: float) -> tuple[str, str] | None:
        """Faellige Aktion als ``(aktion, grund)`` oder ``None``.

        aktion ist entweder ein Modus aus ARMED_STATES oder "disarmed".
        """
        if not self._config.enabled:
            return None
        current = datetime.fromtimestamp(now)
        previous, self._last_check = self._last_check, current
        if previous is None:
            return None

        for value, action in (
            (self._config.arm_at, self._config.arm_mode),
            (self._config.disarm_at, STATE_DISARMED),
        ):
            target = _parse_time(value)
            if target is None:
                continue
            if _crossed(previous, current, target) and self._weekday_ok(current):
                return action, f"Zeitplan {value}"
        return None

    def _weekday_ok(self, moment: datetime) -> bool:
        return not self._config.weekdays or moment.weekday() in self._config.weekdays


def _parse_time(value: str) -> dtime | None:
    try:
        hour, minute = value.split(":")
        return dtime(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def _crossed(previous: datetime, current: datetime, target: dtime) -> bool:
    """Wurde die Uhrzeit zwischen den beiden Zeitpunkten ueberschritten?"""
    if current <= previous:
        return False
    marker = current.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if previous < marker <= current:
        return True
    # Tageswechsel zwischen den beiden Pruefungen abfangen.
    if current.date() != previous.date():
        marker_prev = previous.replace(
            hour=target.hour, minute=target.minute, second=0, microsecond=0
        )
        return previous < marker_prev <= current
    return False


def _mode_label(mode: str) -> str:
    return {
        "armed_home": "Anwesend",
        "armed_away": "Abwesend",
        "armed_night": "Nachtwache",
        STATE_DISARMED: "Unscharf",
    }.get(mode, mode)
