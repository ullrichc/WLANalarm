"""Datenmodell: Messwerte einer WLAN-Funkstrecke.

Eine *Funkstrecke* (Link) ist die Verbindung zwischen einem Access Point
(FRITZ!Box oder ein Mesh-Repeater) und einer Gegenstelle (Client oder ein
weiterer Mesh-Knoten). Jede solche Strecke ist ein potenzieller virtueller
Bewegungsmelder: geht ein Mensch hindurch, aendert sich die Mehrwege-
ausbreitung und damit RSSI, Datenrate und MCS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

BAND_24 = "2.4 GHz"
BAND_5 = "5 GHz"
BAND_6 = "6 GHz"
BAND_UNKNOWN = "unbekannt"

#: Baender, die sich erfahrungsgemaess gut fuer Sensing eignen (kurze Wellenlaenge,
#: mehr Mehrwegeanteil, breitere Kanaele -> reagiert staerker auf Bewegung).
PREFERRED_BANDS = (BAND_6, BAND_5)


def normalise_mac(mac: str | None) -> str:
    """MAC-Adresse auf Grossbuchstaben mit Doppelpunkten normalisieren."""
    if not mac:
        return ""
    cleaned = "".join(ch for ch in mac if ch.isalnum()).upper()
    if len(cleaned) != 12:
        return mac.strip().upper()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def make_link_id(ap_mac: str, peer_mac: str, band: str) -> str:
    """Stabile, richtungsunabhaengige Kennung einer Funkstrecke.

    Die MAC-Adressen werden sortiert, damit dieselbe Strecke unabhaengig davon,
    welche Seite die Mesh-Liste gerade als "Knoten 1" ausweist, dieselbe ID
    bekommt. Das Band gehoert zur ID, weil ein Geraet gleichzeitig auf 5 und
    6 GHz haengen kann und beide Strecken sich voellig unterschiedlich verhalten.
    """
    a, b = sorted((normalise_mac(ap_mac), normalise_mac(peer_mac)))
    return f"{a}_{b}_{band.replace(' ', '')}"


@dataclass(frozen=True, slots=True)
class LinkSample:
    """Ein Messpunkt einer Funkstrecke zu einem Zeitpunkt."""

    ts: float
    link_id: str
    ap_mac: str
    ap_name: str
    peer_mac: str
    peer_name: str
    band: str = BAND_UNKNOWN
    #: Empfangsfeldstaerke in dBm (bevorzugte Messgroesse, aus der Mesh-Liste).
    rssi_dbm: float | None = None
    #: Signalstaerke in Prozent (Fallback aus TR-064, sehr grob quantisiert).
    signal_percent: float | None = None
    #: Signal-Rausch-Abstand in dB, sofern die Box ihn liefert (Feld rx_rsni ab
    #: Mesh-Schema 8.x). Reagiert auf Bewegung oft empfindlicher als die reine
    #: Feldstaerke, weil er die Stoerleistung einbezieht.
    snr_db: float | None = None
    rx_rate_kbps: float | None = None
    tx_rate_kbps: float | None = None
    mcs: int | None = None
    streams: int | None = None
    #: True, wenn die Gegenstelle selbst ein Mesh-Knoten (Repeater) ist.
    peer_is_mesh_node: bool = False

    @property
    def label(self) -> str:
        peer = self.peer_name or self.peer_mac
        ap = self.ap_name or self.ap_mac
        return f"{ap} <-> {peer} ({self.band})"

    @property
    def has_signal(self) -> bool:
        return (
            self.rssi_dbm is not None
            or self.signal_percent is not None
            or self.snr_db is not None
        )

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "link_id": self.link_id,
            "ap_mac": self.ap_mac,
            "ap_name": self.ap_name,
            "peer_mac": self.peer_mac,
            "peer_name": self.peer_name,
            "band": self.band,
            "rssi_dbm": self.rssi_dbm,
            "signal_percent": self.signal_percent,
            "snr_db": self.snr_db,
            "rx_rate_kbps": self.rx_rate_kbps,
            "tx_rate_kbps": self.tx_rate_kbps,
            "mcs": self.mcs,
            "streams": self.streams,
            "peer_is_mesh_node": self.peer_is_mesh_node,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LinkSample":
        return cls(
            ts=float(data["ts"]),
            link_id=data["link_id"],
            ap_mac=data.get("ap_mac", ""),
            ap_name=data.get("ap_name", ""),
            peer_mac=data.get("peer_mac", ""),
            peer_name=data.get("peer_name", ""),
            band=data.get("band", BAND_UNKNOWN),
            rssi_dbm=_opt_float(data.get("rssi_dbm")),
            signal_percent=_opt_float(data.get("signal_percent")),
            snr_db=_opt_float(data.get("snr_db")),
            rx_rate_kbps=_opt_float(data.get("rx_rate_kbps")),
            tx_rate_kbps=_opt_float(data.get("tx_rate_kbps")),
            mcs=_opt_int(data.get("mcs")),
            streams=_opt_int(data.get("streams")),
            peer_is_mesh_node=bool(data.get("peer_is_mesh_node", False)),
        )


@dataclass(slots=True)
class Scan:
    """Alle Funkstrecken zu einem Abtastzeitpunkt."""

    ts: float
    samples: list[LinkSample] = field(default_factory=list)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.samples)

    def __iter__(self) -> Iterable[LinkSample]:  # pragma: no cover - trivial
        return iter(self.samples)

    def by_id(self) -> dict[str, LinkSample]:
        return {s.link_id: s for s in self.samples}


def _opt_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
