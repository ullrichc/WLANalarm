"""Generischer Webhook - fuer Home Assistant, Node-RED, IFTTT, eigene Dienste."""

from __future__ import annotations

import requests

from ..alarm import AlarmEvent
from .base import Notifier, NotifierError


class WebhookNotifier(Notifier):
    type_name = "webhook"

    def send(self, event: AlarmEvent) -> None:
        url = self.option("url", required=True)
        method = str(self.option("method", "POST")).upper()
        timeout = float(self.option("timeout", 10.0))
        headers = dict(self.option("headers", {}) or {})
        headers.setdefault("Content-Type", "application/json")

        try:
            response = requests.request(
                method, url, json=event.to_dict(), headers=headers, timeout=timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise NotifierError(f"Webhook {url}: {exc}") from exc
