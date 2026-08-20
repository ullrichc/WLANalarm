"""Push-Benachrichtigung ueber ntfy (https://ntfy.sh oder eigene Instanz).

Der einfachste Weg zu einer Push-Meldung aufs Handy: Thema abonnieren, fertig.
Wer ntfy.sh oeffentlich nutzt, sollte ein langes, zufaelliges Thema waehlen -
oeffentliche Themen kann jeder mitlesen, der den Namen kennt.
"""

from __future__ import annotations

import requests

from ..alarm import AlarmEvent
from .base import Notifier, NotifierError

_PRIORITY = {"alarm": "urgent", "pending": "high", "motion": "default"}
_TAGS = {"alarm": "rotating_light", "pending": "warning", "motion": "walking"}


class NtfyNotifier(Notifier):
    type_name = "ntfy"

    def send(self, event: AlarmEvent) -> None:
        server = str(self.option("server", "https://ntfy.sh")).rstrip("/")
        topic = self.option("topic", required=True)
        timeout = float(self.option("timeout", 10.0))

        headers = {
            "Title": self.title(event),
            "Priority": _PRIORITY.get(event.type, "default"),
            "Tags": _TAGS.get(event.type, "bell"),
        }
        token = self.option("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if event.type == "alarm" and self.option("click_url"):
            headers["Click"] = str(self.option("click_url"))

        try:
            response = requests.post(
                f"{server}/{topic}",
                data=event.message.encode("utf-8"),
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise NotifierError(f"ntfy: {exc}") from exc
