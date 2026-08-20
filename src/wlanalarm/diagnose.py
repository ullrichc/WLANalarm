"""Diagnose der Mesh-Liste: was liefert diese FRITZ!Box tatsaechlich?

Die Feldnamen in der Mesh-Topologie sind nicht dokumentiert und haben sich
zwischen FRITZ!OS-Staenden schon geaendert. Wenn eine Box keine Messwerte
liefert, beantwortet dieser Bericht die Frage, welche Felder sie stattdessen
verwendet - ohne dass jemand von Hand JSON durchsuchen muss.

Der Bericht ist bewusst so gebaut, dass er weitergegeben werden kann: MAC-
Adressen und Geraetenamen werden durch stabile Platzhalter ersetzt, sodass
die Struktur erkennbar bleibt, aber nicht steht, wer im Haushalt welches
Geraet besitzt.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

#: Schluessel, deren Werte als personenbezogen gelten und ersetzt werden.
_GEHEIM = (
    "mac_address", "device_mac_address", "device_name", "ssid", "uid",
    "node_1_uid", "node_2_uid", "node_interface_1_uid", "node_interface_2_uid",
    "device_serial", "ipv4", "ipv6", "ip_address",
)

#: Felder, die als Messgroesse fuer die Bewegungserkennung taugen koennten.
_INTERESSANT = (
    "rssi", "signal", "strength", "dbm", "rate", "mcs", "nss", "stream",
    "availability", "snr", "quality", "noise", "power",
)


#: Zeichenketten, die als technische Angabe gelten und im Klartext bleiben
#: duerfen, obwohl sie Kleinbuchstaben enthalten.
_TECHNISCH = {
    "ax", "be", "ac", "an", "n", "g", "b", "a", "master", "slave", "client",
    "unknown", "up", "down", "true", "false", "none", "wlan", "lan",
}
#: Alles, was wie eine MAC-Adresse aussieht.
_MAC = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")
#: Zahl mit physikalischer Einheit, etwa "320 MHz" - unbedenklich, enthaelt
#: aber Kleinbuchstaben und faellt deshalb sonst durch die Grossschreibregel.
_MIT_EINHEIT = re.compile(r"^\d+([.,]\d+)?\s*(MHz|GHz|kHz|dBm|dB|Mbit/s|kbit/s|ms|s|%)$")


def _tarnen(wert: Any) -> str:
    """Wert durch einen stabilen, nicht rueckrechenbaren Platzhalter ersetzen."""
    kurz = hashlib.sha256(str(wert).encode("utf-8")).hexdigest()[:6]
    return f"<{kurz}>"


def _sicher(schluessel: str, wert: Any) -> Any:
    """Einen Wert so zurueckgeben, dass der Bericht weitergegeben werden kann.

    Eine feste Liste bekannter Schluessel reicht dafuer nicht: Der Bericht wird
    gerade dann erzeugt, wenn eine FRITZ!OS-Version unbekannte Felder liefert -
    und unter einem unbekannten Schluessel kann ebenso gut ein Geraetename
    stehen. Deshalb entscheidet zusaetzlich der Wert selbst. Zahlen und
    Wahrheitswerte sind unbedenklich; Zeichenketten bleiben nur im Klartext,
    wenn sie wie eine technische Angabe aussehen.
    """
    if schluessel in _GEHEIM:
        return _tarnen(wert)
    if isinstance(wert, bool) or isinstance(wert, (int, float)) or wert is None:
        return wert
    if isinstance(wert, (list, tuple)):
        return [_sicher(schluessel, e) for e in wert]
    if isinstance(wert, dict):
        return {k: _sicher(k, v) for k, v in wert.items()}
    if isinstance(wert, str):
        if _MAC.match(wert.strip()):
            return _tarnen(wert)
        if wert.strip().lower() in _TECHNISCH or _MIT_EINHEIT.match(wert.strip()):
            return wert
        # Technische Kennungen schreibt AVM in Grossbuchstaben, Ziffern und
        # Trennzeichen - "AP_MODE", "BAND_5G", "320 MHz", "WPA3PSK".
        # Alles mit Kleinbuchstaben kann ein frei vergebener Name sein.
        if re.fullmatch(r"[A-Z0-9 _+.:/()\[\]-]*", wert):
            return wert
        return _tarnen(wert)
    return _tarnen(wert)


def bericht(mesh: dict) -> list[str]:
    """Lesbaren Diagnosebericht aus der rohen Mesh-Liste erzeugen."""
    zeilen: list[str] = []
    knoten = [n for n in mesh.get("nodes", []) if isinstance(n, dict)]

    zeilen.append(f"Schema-Version der Mesh-Liste: {mesh.get('schema_version', 'unbekannt')}")
    zeilen.append(f"Knoten insgesamt: {len(knoten)}")

    rollen: dict[str, int] = {}
    for n in knoten:
        rolle = str(n.get("mesh_role", "ohne Angabe"))
        rollen[rolle] = rollen.get(rolle, 0) + 1
    zeilen.append("Rollen: " + ", ".join(f"{r}={z}" for r, z in sorted(rollen.items())))
    zeilen.append("")

    # -- Schnittstellen ---------------------------------------------------- #

    wlan_schnittstellen = []
    for n in knoten:
        for iface in n.get("node_interfaces", []) or []:
            if isinstance(iface, dict) and str(iface.get("type", "")).upper() == "WLAN":
                wlan_schnittstellen.append((n, iface))

    zeilen.append(f"WLAN-Schnittstellen: {len(wlan_schnittstellen)}")
    if wlan_schnittstellen:
        felder = sorted({k for _, i in wlan_schnittstellen for k in i if k != "node_links"})
        zeilen.append("  vorhandene Felder: " + ", ".join(felder))
        beispiel = wlan_schnittstellen[0][1]
        zeilen.append("  Beispiel (eine Schnittstelle):")
        for k in sorted(beispiel):
            if k == "node_links":
                continue
            zeilen.append(f"    {k}: {_sicher(k, beispiel[k])!r}")
    zeilen.append("")

    # -- Verbindungen ------------------------------------------------------ #

    verbindungen = []
    for n, iface in wlan_schnittstellen:
        for link in iface.get("node_links", []) or []:
            if isinstance(link, dict):
                verbindungen.append(link)

    zeilen.append(f"WLAN-Verbindungen: {len(verbindungen)}")
    if not verbindungen:
        zeilen.append("  KEINE - die Box fuehrt in dieser Liste keine WLAN-Verbindungen.")
        return zeilen

    alle_felder = sorted({k for v in verbindungen for k in v})
    zeilen.append("  vorhandene Felder: " + ", ".join(alle_felder))

    messbare = [f for f in alle_felder if any(m in f.lower() for m in _INTERESSANT)]
    zeilen.append("  davon als Messgroesse denkbar: " + (", ".join(messbare) or "KEINE"))
    zeilen.append("")

    # Fuer jedes denkbare Messfeld zeigen, wie oft es belegt ist und mit welchen
    # Werten - daran laesst sich ablesen, was sich als Sensor eignet.
    zeilen.append("  Belegung der Messfelder ueber alle Verbindungen:")
    for feld in messbare:
        werte = [v.get(feld) for v in verbindungen if v.get(feld) not in (None, "")]
        if not werte:
            zeilen.append(f"    {feld:24s} nie belegt")
            continue
        zahlen = [w for w in werte if isinstance(w, (int, float)) and not isinstance(w, bool)]
        if zahlen:
            zeilen.append(
                f"    {feld:24s} {len(werte)}/{len(verbindungen)} belegt, "
                f"Werte von {min(zahlen)} bis {max(zahlen)}"
            )
        else:
            zeilen.append(
                f"    {feld:24s} {len(werte)}/{len(verbindungen)} belegt, "
                f"Beispiel {werte[0]!r}"
            )
    zeilen.append("")

    zeilen.append("  Eine vollstaendige Verbindung als Beispiel:")
    beispiel = max(verbindungen, key=len)
    for k in sorted(beispiel):
        zeilen.append(f"    {k}: {_sicher(k, beispiel[k])!r}")
    zeilen.append("")

    zeilen.extend(_urteil(mesh))
    return zeilen


def _urteil(mesh: dict) -> list[str]:
    """Nachvollziehen, was der Parser aus jeder Verbindung macht.

    Beantwortet die Frage, an welcher Stelle eine Verbindung verloren geht -
    beim Zustand, bei der Zuordnung der Schnittstellen oder beim Messwert.
    """
    from .sources.mesh_parser import detect_band, parse_mesh_list

    zeilen = ["Was WLANalarm daraus macht:"]

    schnittstellen: dict[str, tuple[dict, dict]] = {}
    for knoten in mesh.get("nodes", []) or []:
        if not isinstance(knoten, dict):
            continue
        for iface in knoten.get("node_interfaces", []) or []:
            if isinstance(iface, dict) and iface.get("uid"):
                schnittstellen[iface["uid"]] = (knoten, iface)

    gesehen: set[tuple] = set()
    for knoten, iface in schnittstellen.values():
        art = str(iface.get("type", "")).upper()
        for link in iface.get("node_links", []) or []:
            if not isinstance(link, dict):
                continue
            kennung = tuple(sorted((
                str(link.get("node_interface_1_uid")),
                str(link.get("node_interface_2_uid")),
            )))
            if kennung in gesehen:
                continue
            gesehen.add(kennung)

            gegen = _gegenstelle(link, iface, schnittstellen)
            name = _tarnen(gegen[0].get("device_name", "?")) if gegen else "?"
            rolle = str(gegen[0].get("mesh_role", "?")) if gegen else "?"
            beschreibung = f"    {name} (Rolle {rolle}, Schnittstelle {art})"

            if art != "WLAN":
                zeilen.append(f"{beschreibung}: uebersprungen, keine WLAN-Schnittstelle")
                continue
            zustand = str(link.get("state", "")).upper()
            if zustand not in ("CONNECTED", "ACTIVE", "UP", ""):
                zeilen.append(f"{beschreibung}: uebersprungen, Zustand {zustand!r}")
                continue
            if gegen is None:
                zeilen.append(f"{beschreibung}: verworfen, Gegenstelle nicht auffindbar")
                continue
            band = detect_band(iface) or detect_band(gegen[1]) or "unbekannt"
            messwert = next(
                (f"{k}={link[k]}" for k in ("rssi", "cur_rssi", "signal_strength") if link.get(k)),
                None,
            )
            rate = link.get("cur_data_rate_rx")
            teile = [f"Band {band}"]
            teile.append(messwert if messwert else "OHNE Signalwert")
            teile.append(f"Datenrate {rate}" if rate else "ohne Datenrate")
            zeilen.append(f"{beschreibung}: {', '.join(teile)}")

    scan = parse_mesh_list(mesh, 0.0)
    zeilen.append("")
    zeilen.append(f"  Ergebnis: {len(scan.samples)} Funkstrecke(n) erkannt, "
                  f"davon {sum(1 for s in scan.samples if s.has_signal)} mit Messwert.")
    return zeilen


def _gegenstelle(link: dict, iface: dict, schnittstellen: dict):
    """Die andere Seite einer Verbindung ermitteln."""
    for schluessel in ("node_interface_1_uid", "node_interface_2_uid"):
        andere = schnittstellen.get(link.get(schluessel))
        if andere is not None and andere[1].get("uid") != iface.get("uid"):
            return andere
    return None
