"""TR-064-Fallback ueber den Dienst WLANConfiguration.

Diese Quelle kommt zum Zug, wenn die Mesh-Liste nicht verfuegbar ist. Sie ist
deutlich schwaecher:

* Die Signalstaerke kommt nur in Prozent und ist grob quantisiert - feine
  Schwankungen, aus denen sich Bewegung ableiten liesse, gehen dabei verloren.
* Es werden nur Geraete erfasst, die direkt an der Box haengen, nicht die an
  einem Repeater.

Als zusaetzliches Merkmal wird deshalb die ausgehandelte Datenrate mitgefuehrt,
die feiner aufgeloest ist als die Prozentangabe.
"""

from __future__ import annotations

import logging
import time

from ..model import BAND_5, BAND_24, BAND_6, BAND_UNKNOWN, LinkSample, Scan, make_link_id, normalise_mac
from .base import SourceError
from .fritz_base import FritzSource

log = logging.getLogger(__name__)

#: WLANConfiguration-Instanzen einer FRITZ!Box: 1 = 2,4 GHz, 2 = 5 GHz,
#: 3 = Gastnetz oder 6 GHz (modellabhaengig), 4 = 6 GHz bei Tri-Band-Modellen
#: wie der FRITZ!Box 5690 Pro. Das tatsaechliche Band wird zur Laufzeit aus
#: GetInfo ermittelt; diese Tabelle ist nur der Ausgangspunkt.
_MAX_WLAN_INSTANCES = 4


class FritzTr064Source(FritzSource):
    name = "tr064"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._instances: list[tuple[int, str, str]] | None = None

    def _discover_instances(self) -> list[tuple[int, str, str]]:
        """Aktive WLANConfiguration-Instanzen samt Band und AP-MAC ermitteln."""
        if self._instances is not None:
            return self._instances
        connection = self._connect()
        found: list[tuple[int, str, str]] = []
        for index in range(1, _MAX_WLAN_INSTANCES + 1):
            service = f"WLANConfiguration:{index}"
            if service.replace(":", "") not in connection.services:
                continue
            try:
                info = connection.call_action(service, "GetInfo")
            except Exception:
                continue
            if not info.get("NewEnable"):
                continue
            band = _band_from_info(info)
            ap_mac = normalise_mac(info.get("NewBSSID", "")) or f"WLAN{index}"
            found.append((index, band, ap_mac))
        if not found:
            raise SourceError("Keine aktive WLANConfiguration-Instanz gefunden")
        self._instances = found
        return found

    def check(self) -> str:
        connection = self._connect()
        info = connection.call_action("DeviceInfo:1", "GetInfo")
        instances = self._discover_instances()
        bands = ", ".join(band for _, band, _ in instances)
        return (
            f"{info.get('NewModelName', 'unbekannt')}, "
            f"FRITZ!OS {info.get('NewSoftwareVersion', '?')} (Baender: {bands})"
        )

    def scan(self) -> Scan:
        connection = self._connect()
        ts = time.time()
        samples: list[LinkSample] = []
        for index, band, ap_mac in self._discover_instances():
            service = f"WLANConfiguration:{index}"
            try:
                total = connection.call_action(service, "GetTotalAssociations")
                count = int(total.get("NewTotalAssociations", 0))
            except Exception as exc:
                self._connection = None
                raise SourceError(f"{service}: Abfrage fehlgeschlagen: {exc}") from exc

            for position in range(count):
                try:
                    entry = connection.call_action(
                        service,
                        "GetGenericAssociatedDeviceInfo",
                        NewAssociatedDeviceIndex=position,
                    )
                except Exception:
                    # Die Liste kann sich waehrend der Abfrage aendern.
                    continue
                sample = _entry_to_sample(entry, ts, band, ap_mac)
                if sample is not None:
                    samples.append(sample)
        return Scan(ts=ts, samples=samples)


def _band_from_info(info: dict) -> str:
    """Band aus dem GetInfo-Ergebnis einer WLANConfiguration-Instanz ableiten."""
    standard = str(info.get("NewStandard", "")).lower()
    channel = info.get("NewChannel")

    if standard in ("b", "g", "bg", "gn", "bgn"):
        return BAND_24
    try:
        channel_no = int(channel)
    except (TypeError, ValueError):
        return BAND_UNKNOWN
    if 1 <= channel_no <= 14:
        return BAND_24
    if 36 <= channel_no <= 196:
        return BAND_5
    if standard in ("be", "ax") and channel_no > 196:
        return BAND_6
    return BAND_UNKNOWN


def _entry_to_sample(entry: dict, ts: float, band: str, ap_mac: str) -> LinkSample | None:
    if not entry.get("NewAssociatedDeviceAuthState"):
        return None
    peer_mac = normalise_mac(entry.get("NewAssociatedDeviceMACAddress", ""))
    if not peer_mac:
        return None
    percent = entry.get("NewX_AVM-DE_SignalStrength")
    speed = entry.get("NewX_AVM-DE_Speed")
    try:
        percent_value = float(percent) if percent not in (None, "") else None
    except (TypeError, ValueError):
        percent_value = None
    try:
        # NewX_AVM-DE_Speed ist in Mbit/s angegeben.
        speed_kbps = float(speed) * 1000 if speed not in (None, "") else None
    except (TypeError, ValueError):
        speed_kbps = None

    return LinkSample(
        ts=ts,
        link_id=make_link_id(ap_mac, peer_mac, band),
        ap_mac=ap_mac,
        ap_name="FRITZ!Box",
        peer_mac=peer_mac,
        peer_name=str(entry.get("NewAssociatedDeviceIPAddress", "") or peer_mac),
        band=band,
        rssi_dbm=None,
        signal_percent=percent_value,
        rx_rate_kbps=speed_kbps,
        tx_rate_kbps=speed_kbps,
        peer_is_mesh_node=False,
    )
