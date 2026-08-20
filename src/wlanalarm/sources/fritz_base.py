"""Gemeinsame TR-064-Verbindung fuer die FRITZ!Box-Quellen."""

from __future__ import annotations

import logging

from .base import SampleSource, SourceError

log = logging.getLogger(__name__)


class FritzSource(SampleSource):
    """Haelt die TR-064-Sitzung und uebersetzt Verbindungsfehler in
    Meldungen, mit denen man auch ohne Netzwerkkenntnisse etwas anfangen kann."""

    def __init__(
        self,
        address: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool = False,
        timeout: float = 10.0,
    ) -> None:
        self._address = address
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout
        self._connection = None

    def _connect(self):
        if self._connection is not None:
            return self._connection
        try:
            from fritzconnection import FritzConnection
        except ImportError as exc:  # pragma: no cover - Abhaengigkeit fehlt
            raise SourceError(
                "Das Paket 'fritzconnection' ist nicht installiert "
                "(pip install fritzconnection)"
            ) from exc
        try:
            self._connection = FritzConnection(
                address=self._address,
                port=self._port,
                user=self._username,
                password=self._password,
                use_tls=self._use_tls,
                timeout=self._timeout,
            )
        except Exception as exc:
            raise SourceError(_erklaere(exc, self._address, self._port)) from exc
        return self._connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        session = getattr(connection, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # pragma: no cover - beim Beenden egal
                pass


def _erklaere(exc: Exception, address: str, port: int) -> str:
    """Aus einer Bibliotheksausnahme eine brauchbare Handlungsanweisung machen."""
    text = str(exc).lower()
    ziel = f"{address}:{port}"
    if "timed out" in text or "timeout" in text or "max retries" in text:
        return (
            f"{ziel} antwortet nicht. Pruefe, ob die Adresse stimmt und ob in der "
            f"FRITZ!Box unter Heimnetz > Netzwerk > Netzwerkeinstellungen "
            f"'Zugriff fuer Anwendungen zulassen' aktiviert ist."
        )
    if "401" in text or "unauthorized" in text or "authoriz" in text:
        return (
            f"{ziel} weist die Anmeldung zurueck. Pruefe Benutzername und Kennwort "
            f"und ob der Benutzer die Berechtigung 'FRITZ!Box Einstellungen' hat."
        )
    if "name or service not known" in text or "nodename" in text:
        return f"Der Name in fritzbox.address ({address}) laesst sich nicht aufloesen."
    return f"Verbindung zu {ziel} fehlgeschlagen: {exc}"
