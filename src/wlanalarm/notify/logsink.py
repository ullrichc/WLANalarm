"""Ausgabe ins Programmlog - der Standardkanal, immer verfuegbar."""

from __future__ import annotations

import logging

from ..alarm import AlarmEvent
from .base import Notifier

log = logging.getLogger("wlanalarm.event")


class LogNotifier(Notifier):
    type_name = "log"

    def send(self, event: AlarmEvent) -> None:
        level = logging.WARNING if event.level == "alarm" else logging.INFO
        log.log(level, "[%s] %s", event.type, event.message)
