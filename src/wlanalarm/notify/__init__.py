"""Benachrichtigungskanaele und ihre Erzeugung aus der Konfiguration."""

from __future__ import annotations

import logging

from ..config import Config, NotifierConfig
from .base import NotificationHub, Notifier, NotifierError
from .logsink import LogNotifier
from .ntfy import NtfyNotifier
from .smtp import SmtpNotifier
from .webhook import WebhookNotifier

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type[Notifier]] = {
    "log": LogNotifier,
    "ntfy": NtfyNotifier,
    "webhook": WebhookNotifier,
    "smtp": SmtpNotifier,
}


def _mqtt_class() -> type[Notifier]:
    from .mqtt import MqttNotifier

    return MqttNotifier


def build_notifier(config: NotifierConfig) -> Notifier:
    """Einen einzelnen Kanal erzeugen."""
    if config.type == "mqtt":
        return _mqtt_class()(config)
    factory = _REGISTRY.get(config.type)
    if factory is None:
        raise NotifierError(
            f"Unbekannter Benachrichtigungstyp {config.type!r}; "
            f"moeglich sind {sorted(list(_REGISTRY) + ['mqtt'])}"
        )
    return factory(config)


def build_hub(config: Config) -> NotificationHub:
    """Alle konfigurierten Kanaele erzeugen.

    Ohne Konfiguration wird der Log-Kanal eingerichtet, damit Ereignisse
    nicht spurlos verschwinden.
    """
    entries = config.notifiers or [NotifierConfig(type="log", min_level="motion")]
    notifiers: list[Notifier] = []
    for entry in entries:
        if not entry.enabled:
            continue
        try:
            notifiers.append(build_notifier(entry))
        except NotifierError as exc:
            log.error("Kanal %s uebersprungen: %s", entry.name or entry.type, exc)
    if not notifiers:
        notifiers.append(LogNotifier(NotifierConfig(type="log", min_level="motion")))
    return NotificationHub(notifiers)


__all__ = [
    "NotificationHub",
    "Notifier",
    "NotifierError",
    "build_hub",
    "build_notifier",
]
