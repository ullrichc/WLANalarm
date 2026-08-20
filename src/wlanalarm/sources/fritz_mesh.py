"""Mesh-Liste der FRITZ!Box als Datenquelle.

Das ist die bevorzugte Quelle: sie liefert den RSSI in dBm sowie Datenrate,
MCS und Streamzahl je Funkstrecke - und zwar auch fuer Strecken, die an einem
Mesh-Repeater haengen und nicht direkt an der Box.

Voraussetzungen in der FRITZ!Box:
  * Heimnetz > Netzwerk > Netzwerkeinstellungen > "Zugriff fuer Anwendungen zulassen"
  * ein Benutzer mit der Berechtigung "FRITZ!Box Einstellungen"
"""

from __future__ import annotations

import logging
import time

from ..model import Scan
from .base import SourceError
from .fritz_base import FritzSource
from .mesh_parser import parse_mesh_list

log = logging.getLogger(__name__)


class FritzMeshSource(FritzSource):
    name = "mesh"

    def check(self) -> str:
        """Verbindung pruefen und Modell-/Firmwarekennung zurueckgeben."""
        connection = self._connect()
        try:
            info = connection.call_action("DeviceInfo:1", "GetInfo")
        except Exception as exc:
            raise SourceError(
                f"TR-064 antwortet nicht wie erwartet: {exc}. Pruefe Benutzername, "
                f"Passwort und die Berechtigung 'FRITZ!Box Einstellungen'."
            ) from exc
        model = info.get("NewModelName", "unbekannt")
        version = info.get("NewSoftwareVersion", "?")
        actions = connection.services.get("Hosts1")
        if actions is None or "X_AVM-DE_GetMeshListPath" not in actions.actions:
            raise SourceError(
                f"{model} (FRITZ!OS {version}) bietet keine Mesh-Liste an. "
                f"Setze fritzbox.source auf 'tr064'."
            )
        return f"{model}, FRITZ!OS {version}"

    # -- Abfrage ----------------------------------------------------------- #

    def fetch_mesh_list(self) -> dict:
        """Rohes Mesh-JSON holen."""
        connection = self._connect()
        try:
            result = connection.call_action("Hosts:1", "X_AVM-DE_GetMeshListPath")
            path = result["NewX_AVM-DE_MeshListPath"]
        except Exception as exc:
            self._connection = None  # beim naechsten Versuch neu anmelden
            raise SourceError(f"Mesh-Pfad konnte nicht abgefragt werden: {exc}") from exc

        url = f"{connection.address}:{connection.port}{path}"
        try:
            response = connection.session.get(url, timeout=self._timeout)
            if response.status_code != 200:
                raise SourceError(
                    f"Mesh-Liste lieferte HTTP {response.status_code}. Der verwendete "
                    f"Benutzer hat vermutlich keinen Zugriff auf die Topologie."
                )
            return response.json()
        except SourceError:
            raise
        except Exception as exc:
            self._connection = None
            raise SourceError(f"Mesh-Liste konnte nicht geladen werden: {exc}") from exc

    def scan(self) -> Scan:
        data = self.fetch_mesh_list()
        scan = parse_mesh_list(data, time.time())
        if not scan.samples:
            log.debug("Mesh-Liste enthielt keine verbundene WLAN-Strecke")
        return scan
