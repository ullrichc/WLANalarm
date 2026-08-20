"""Auswahl der Funkstrecken, die als virtuelle Bewegungsmelder dienen.

Nicht jede WLAN-Verbindung taugt dafuer. Brauchbar ist eine Strecke, deren
beide Enden stillstehen und dauerhaft eingeschaltet sind - Fernseher,
Lautsprecher, Drucker, Kamera, Steckdose und vor allem Mesh-Repeater. Un-
brauchbar sind Smartphones und Notebooks: sie wandern mit ihren Besitzern
durch die Wohnung und legen sich zwischendurch schlafen, was der Detektor
nicht von Bewegung unterscheiden kann.

Die Voreinstellungen setzen deshalb dieselbe Regel um, die auch Comcast
seinen Kundinnen und Kunden gibt: nur stationaere Geraete verwenden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import re

from .config import Config
from .model import LinkSample, PREFERRED_BANDS, Scan

log = logging.getLogger(__name__)

#: Namensmuster persoenlicher Geraete, etwa "Handy-von-Alex" oder
#: "Alex's Tablet". Ein Geraet, das den Namen seines Besitzers traegt, wird in
#: aller Regel auch mit ihm herumgetragen.
#:
#: Der deutsche Genitiv ohne Apostroph ("Alex Tablet") ist bewusst NICHT
#: erfasst: Ein Muster dafuer trifft jedes Wort, das auf s endet, und damit
#: auch "Sonos Wohnzimmer" oder "Philips Hue" - also gerade die stationaeren
#: Geraete, um die es hier geht.
_PERSOENLICH = re.compile(r"(^|[-_ ])von[-_ ]\w|\w[\u2019'`]s[-_ ]")


@dataclass
class Candidate:
    """Eine moegliche Sensorstrecke samt Begruendung der Entscheidung."""

    sample: LinkSample
    selected: bool
    reason: str
    zone: str = "default"
    weight: float = 1.0
    configured: bool = False

    @property
    def link_id(self) -> str:
        return self.sample.link_id

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "label": self.sample.label,
            "peer_mac": self.sample.peer_mac,
            "peer_name": self.sample.peer_name,
            "band": self.sample.band,
            "rssi_dbm": self.sample.rssi_dbm,
            "is_mesh_node": self.sample.peer_is_mesh_node,
            "selected": self.selected,
            "reason": self.reason,
            "zone": self.zone,
            "weight": self.weight,
            "configured": self.configured,
        }


def evaluate(scan: Scan, config: Config) -> list[Candidate]:
    """Alle Strecken eines Scans bewerten - ausgewaehlte wie verworfene.

    Die verworfenen bleiben in der Liste, damit ``wlanalarm discover``
    erklaeren kann, warum ein Geraet nicht verwendet wird.
    """
    selection = config.selection
    candidates: list[Candidate] = []

    for sample in scan.samples:
        link_config = config.link_config(sample.peer_mac)
        configured = link_config is not None
        zone = link_config.zone if link_config else "default"
        weight = link_config.weight if link_config else 1.0

        def make(selected: bool, reason: str) -> Candidate:
            return Candidate(
                sample=sample,
                selected=selected,
                reason=reason,
                zone=zone,
                weight=weight,
                configured=configured,
            )

        if link_config is not None and link_config.ignore:
            candidates.append(make(False, "in der Konfiguration ausgeschlossen"))
            continue
        if sample.peer_mac in selection.ignore_macs:
            candidates.append(make(False, "MAC-Adresse steht auf der Sperrliste"))
            continue
        if not sample.has_signal:
            candidates.append(make(False, "kein Signalwert verfügbar"))
            continue

        is_mesh = sample.peer_is_mesh_node and selection.always_use_mesh_nodes

        # Ausdruecklich konfigurierte Geraete und Mesh-Knoten umgehen die
        # Heuristiken - dort hat entweder der Mensch oder die Topologie entschieden.
        if not configured and not is_mesh:
            if selection.only_configured:
                candidates.append(make(False, "nicht in der Konfiguration aufgeführt (only_configured)"))
                continue
            if selection.bands and sample.band not in selection.bands:
                candidates.append(make(False, f"Band {sample.band} ist nicht ausgewählt"))
                continue
            name = (sample.peer_name or "").lower()
            hit = next((s for s in selection.ignore_name_contains if s and s in name), None)
            if hit:
                candidates.append(
                    make(False, f"Name enthält '{hit}' - vermutlich ein mobiles Gerät")
                )
                continue
            if selection.ignore_personal_names and _PERSOENLICH.search(name):
                candidates.append(
                    make(False, "Name nennt eine Person - vermutlich ein persönliches Gerät")
                )
                continue

        if weight <= 0:
            candidates.append(make(False, "Gewicht 0 - wird beobachtet, löst aber nie aus"))
            continue

        reason = (
            "konfiguriert"
            if configured
            else ("Mesh-Knoten" if is_mesh else "ortsfestes Gerät")
        )
        candidates.append(make(True, reason))

    chosen = [c for c in candidates if c.selected]
    chosen.sort(key=_rank, reverse=True)
    if len(chosen) > selection.max_links:
        for candidate in chosen[selection.max_links :]:
            candidate.selected = False
            candidate.reason = f"über der Grenze von max_links={selection.max_links}"
        log.info(
            "%d Funkstrecken verfuegbar, auf %d begrenzt",
            len(chosen),
            selection.max_links,
        )
    return candidates


def _rank(candidate: Candidate) -> tuple:
    """Sortierschluessel: je hoeher, desto eher wird die Strecke verwendet."""
    sample = candidate.sample
    band_rank = len(PREFERRED_BANDS) - PREFERRED_BANDS.index(sample.band) if sample.band in PREFERRED_BANDS else 0
    # Ein starkes Signal ist stabiler und damit ein besserer Sensor als ein
    # grenzwertiges, das ohnehin dauernd schwankt.
    signal = sample.rssi_dbm if sample.rssi_dbm is not None else -100.0
    return (
        1 if candidate.configured else 0,
        1 if sample.peer_is_mesh_node else 0,
        band_rank,
        signal,
    )


def selected_ids(candidates: list[Candidate]) -> set[str]:
    return {c.link_id for c in candidates if c.selected}
