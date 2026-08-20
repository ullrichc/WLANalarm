"""E-Mail-Benachrichtigung ueber SMTP."""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from ..alarm import AlarmEvent
from .base import Notifier, NotifierError


class SmtpNotifier(Notifier):
    type_name = "smtp"

    def send(self, event: AlarmEvent) -> None:
        host = self.option("host", required=True)
        port = int(self.option("port", 587))
        sender = self.option("from", required=True)
        recipients = self.option("to", required=True)
        if isinstance(recipients, str):
            recipients = [recipients]
        username = self.option("username", sender)
        password = self.option("password")
        use_starttls = bool(self.option("starttls", True))
        timeout = float(self.option("timeout", 20.0))

        message = EmailMessage()
        message["Subject"] = self.title(event)
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        when = datetime.fromtimestamp(event.ts).strftime("%d.%m.%Y %H:%M:%S")
        body = [
            event.message,
            "",
            f"Zeitpunkt: {when}",
            f"Modus:     {event.mode}",
        ]
        if event.zones:
            body.append(f"Zonen:     {', '.join(event.zones)}")
        if event.links:
            body.append(f"Strecken:  {', '.join(event.links)}")
        if event.score:
            body.append(f"Score:     {event.score:.2f}")
        message.set_content("\n".join(body))

        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
            with server:
                if port != 465 and use_starttls:
                    server.starttls(context=ssl.create_default_context())
                if password:
                    server.login(username, password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifierError(f"SMTP {host}:{port}: {exc}") from exc
