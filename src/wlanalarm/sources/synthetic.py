"""Synthetische Funkstrecken fuer Tests und Trockenuebungen.

Modelliert das, was man an einer echten FRITZ!Box misst: einen im Wesentlichen
konstanten RSSI mit leichtem Rauschen und langsamer Drift, ueberlagert von
kurzen Phasen deutlich erhoehter Streuung, wenn jemand durch die Funkstrecke
laeuft.
"""

from __future__ import annotations

import math
import random

from ..model import LinkSample, Scan, make_link_id
from .base import SampleSource


class SyntheticLink:
    """Beschreibung einer simulierten Funkstrecke."""

    def __init__(
        self,
        peer_name: str,
        peer_mac: str,
        band: str = "5 GHz",
        base_rssi: float = -55.0,
        quiet_noise_db: float = 0.4,
        motion_noise_db: float = 4.0,
        ap_mac: str = "34:31:C4:00:00:05",
        ap_name: str = "fritz.box",
        peer_is_mesh_node: bool = False,
        base_rate_kbps: float = 866000.0,
    ) -> None:
        self.peer_name = peer_name
        self.peer_mac = peer_mac
        self.band = band
        self.base_rssi = base_rssi
        self.quiet_noise_db = quiet_noise_db
        self.motion_noise_db = motion_noise_db
        self.ap_mac = ap_mac
        self.ap_name = ap_name
        self.peer_is_mesh_node = peer_is_mesh_node
        self.base_rate_kbps = base_rate_kbps

    @property
    def link_id(self) -> str:
        return make_link_id(self.ap_mac, self.peer_mac, self.band)


class SyntheticSource(SampleSource):
    """Erzeugt reproduzierbare Messreihen.

    Args:
        links: die simulierten Funkstrecken.
        interval: Abstand zwischen zwei Messpunkten in Sekunden.
        motion_windows: Liste von ``(start, ende)`` in Sekunden ab Startzeit,
            in denen Bewegung simuliert wird.
        motion_links: nur diese Gegenstellen-MACs reagieren auf Bewegung;
            ``None`` = alle.
        seed: macht die Reihe reproduzierbar.
    """

    name = "synthetic"

    def __init__(
        self,
        links: list[SyntheticLink],
        interval: float = 2.0,
        motion_windows: list[tuple[float, float]] | None = None,
        motion_links: list[str] | None = None,
        seed: int = 1234,
        start_ts: float = 1_700_000_000.0,
    ) -> None:
        self.links = links
        self.interval = interval
        self.motion_windows = motion_windows or []
        self.motion_links = motion_links
        self.start_ts = start_ts
        self._random = random.Random(seed)
        self._tick = 0

    @property
    def elapsed(self) -> float:
        return self._tick * self.interval

    def is_motion(self, elapsed: float) -> bool:
        return any(start <= elapsed < end for start, end in self.motion_windows)

    def scan(self) -> Scan:
        elapsed = self.elapsed
        ts = self.start_ts + elapsed
        moving = self.is_motion(elapsed)
        samples = []
        for link in self.links:
            affected = moving and (
                self.motion_links is None or link.peer_mac in self.motion_links
            )
            noise = link.motion_noise_db if affected else link.quiet_noise_db
            # Langsame Drift bildet Temperatur- und Umgebungseffekte nach.
            drift = 0.8 * math.sin(elapsed / 900.0)
            rssi = link.base_rssi + drift + self._random.gauss(0.0, noise)
            # Der Stoerabstand folgt derselben Ursache wie die Feldstaerke,
            # reagiert aber etwas staerker - so verhaelt er sich auch real.
            snr = 40.0 + (rssi - link.base_rssi) * 1.3 + self._random.gauss(0.0, noise * 0.5)
            # Die Datenrate folgt dem RSSI grob und springt in Stufen.
            rate_factor = 1.0 + (rssi - link.base_rssi) / 40.0
            if affected:
                rate_factor *= self._random.choice((0.5, 0.75, 1.0))
            samples.append(
                LinkSample(
                    ts=ts,
                    link_id=link.link_id,
                    ap_mac=link.ap_mac,
                    ap_name=link.ap_name,
                    peer_mac=link.peer_mac,
                    peer_name=link.peer_name,
                    band=link.band,
                    rssi_dbm=round(rssi, 1),
                    snr_db=round(max(0.0, snr), 1),
                    rx_rate_kbps=round(link.base_rate_kbps * max(rate_factor, 0.1)),
                    tx_rate_kbps=round(link.base_rate_kbps * max(rate_factor, 0.1)),
                    peer_is_mesh_node=link.peer_is_mesh_node,
                )
            )
        self._tick += 1
        return Scan(ts=ts, samples=samples)

    def close(self) -> None:  # pragma: no cover - nichts freizugeben
        pass
