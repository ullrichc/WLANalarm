"""Anwesenheitserkennung ueber bekannte Geraete im Heimnetz.

Damit schaltet sich die Anlage selbst scharf, wenn alle Bewohner das Haus
verlassen haben - das Gegenstueck zu Comcasts Home/Away-Umschaltung.

Wichtige Einschraenkung: aktuelle Smartphones melden sich mit einer
zufaelligen MAC-Adresse an. Fuer die hinterlegten Geraete muss die
MAC-Randomisierung fuer das eigene WLAN abgeschaltet werden ("Private
WLAN-Adresse" unter iOS, "Zufaellige MAC verwenden" unter Android), sonst
aendert sich die Adresse und die Erkennung schlaegt fehl.
"""

from __future__ import annotations

import logging

from .config import PresenceConfig
from .model import normalise_mac
from .sources.base import SourceError

log = logging.getLogger(__name__)


class PresenceTracker:
    """Fragt zyklisch ab, ob eines der Anwesenheitsgeraete online ist."""

    def __init__(self, config: PresenceConfig, connection_factory) -> None:
        """
        Args:
            config: die Anwesenheitseinstellungen.
            connection_factory: liefert eine verbundene ``FritzConnection``.
                Wird als Funktion uebergeben, damit sich der Tracker ohne
                FRITZ!Box testen laesst.
        """
        self._config = config
        self._factory = connection_factory
        self._present = True
        self._last_seen = 0.0
        self._last_poll = 0.0
        self._poll_interval = 30.0

    @property
    def present(self) -> bool:
        return self._present

    def poll(self, now: float) -> bool | None:
        """Anwesenheit pruefen. Gibt den neuen Zustand zurueck, wenn er sich
        geaendert hat, sonst ``None``."""
        if not self._config.enabled:
            return None
        if now - self._last_poll < self._poll_interval:
            return None
        self._last_poll = now

        try:
            any_online = self._any_device_online()
        except SourceError as exc:
            log.warning("Anwesenheitspruefung fehlgeschlagen: %s", exc)
            return None

        if any_online:
            self._last_seen = now
            if not self._present:
                self._present = True
                log.info("Anwesenheit erkannt")
                return True
            return None

        if self._present and (now - self._last_seen) >= self._config.away_after_seconds:
            self._present = False
            log.info(
                "Seit %.0f s kein bekanntes Geraet im Netz - niemand zuhause",
                now - self._last_seen,
            )
            return False
        return None

    def _any_device_online(self) -> bool:
        connection = self._factory()
        for mac in self._config.macs:
            try:
                result = connection.call_action(
                    "Hosts:1",
                    "GetSpecificHostEntry",
                    NewMACAddress=normalise_mac(mac),
                )
            except Exception as exc:
                # Unbekannte MAC quittiert die Box mit einem Fehler - das ist
                # kein Problem, das Geraet ist dann schlicht nicht da.
                log.debug("Host %s nicht abfragbar: %s", mac, exc)
                continue
            if result.get("NewActive"):
                return True
        return False
