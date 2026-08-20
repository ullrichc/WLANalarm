"""Gemeinsame Testbausteine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wlanalarm.config import Config, config_from_dict
from wlanalarm.sources.synthetic import SyntheticLink, SyntheticSource

DATA = Path(__file__).parent / "data"


@pytest.fixture
def mesh_json() -> dict:
    return json.loads((DATA / "mesh_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def mesh_json_schema87() -> dict:
    """Mesh-Liste einer FRITZ!Box 5690 Pro mit FRITZ!OS 8.2x (Schema 8.7)."""
    return json.loads((DATA / "mesh_sample_schema87.json").read_text(encoding="utf-8"))


@pytest.fixture
def base_config() -> Config:
    return config_from_dict({"fritzbox": {"username": "test", "password": "test"}})


def make_config(**overrides) -> Config:
    """Konfiguration mit den Pflichtfeldern und beliebigen Abweichungen."""
    raw = {"fritzbox": {"username": "test", "password": "test"}}
    raw.update(overrides)
    return config_from_dict(raw)


def quiet_links() -> list[SyntheticLink]:
    """Drei stationaere, ruhige Funkstrecken - der Normalfall."""
    return [
        SyntheticLink("Repeater Flur", "34:31:C4:00:00:20", base_rssi=-47,
                      quiet_noise_db=0.35, motion_noise_db=3.5, peer_is_mesh_node=True),
        SyntheticLink("Fernseher", "AA:BB:CC:00:00:03", base_rssi=-58,
                      quiet_noise_db=0.5, motion_noise_db=4.0),
        SyntheticLink("Lautsprecher", "AA:BB:CC:00:00:04", band="6 GHz", base_rssi=-52,
                      quiet_noise_db=0.4, motion_noise_db=4.5),
    ]


def run_source(detector, source: SyntheticSource, ticks: int, config: Config):
    """``ticks`` Messzyklen durch den Detektor schicken und die Ergebnisse sammeln."""
    from wlanalarm.selection import evaluate, selected_ids

    results = []
    for _ in range(ticks):
        scan = source.scan()
        results.append(detector.update(scan, selected_ids(evaluate(scan, config))))
    return results
