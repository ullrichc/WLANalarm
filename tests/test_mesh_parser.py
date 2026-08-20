import pytest

from wlanalarm.model import BAND_5, BAND_6, BAND_24
from wlanalarm.sources.mesh_parser import detect_band, parse_mesh_list


@pytest.fixture
def scan(mesh_json):
    return parse_mesh_list(mesh_json, ts=1000.0)


def test_findet_alle_verbundenen_strecken(scan):
    assert len(scan.samples) == 5


def test_doppelt_gelistete_strecke_wird_zusammengefuehrt(scan):
    """Die Box-zu-Repeater-Strecke steht in beiden Knoten - sie darf nur
    einmal erscheinen, und zwar mit dem RSSI aus der AP-Sicht."""
    repeater = [s for s in scan.samples if s.peer_name == "Repeater Flur"]
    assert len(repeater) == 1
    assert repeater[0].rssi_dbm == -47.0
    assert repeater[0].peer_is_mesh_node is True


def test_getrennte_strecken_werden_verworfen(scan):
    assert not any(s.peer_name == "Altes Tablet" for s in scan.samples)


def test_strecke_hinter_dem_repeater_wird_erkannt(scan):
    """Genau das kann die TR-064-Abfrage nicht - dort sind Geraete am
    Repeater unsichtbar."""
    kamera = next(s for s in scan.samples if s.peer_name == "Kamera Eingang")
    assert kamera.ap_name == "Repeater Flur"
    assert kamera.rssi_dbm == -61.0


def test_baender_werden_korrekt_zugeordnet(scan):
    bands = {s.peer_name: s.band for s in scan.samples}
    assert bands["Fernseher"] == BAND_5
    assert bands["Sonos Wohnzimmer"] == BAND_6
    assert bands["Steckdose Kueche"] == BAND_24


def test_leeres_dokument_ergibt_leeren_scan():
    assert parse_mesh_list({}, ts=1.0).samples == []


def test_band_erkennung_faellt_auf_namen_und_kanal_zurueck():
    assert detect_band({"name": "WLAN 6GHz"}) == BAND_6
    assert detect_band({"current_channel": 100}) == BAND_5
    assert detect_band({"current_channel": 6}) == BAND_24
    assert detect_band({"name": "WLAN"}) is None


def test_rssi_ohne_vorzeichen_wird_korrigiert():
    """Manche Firmwarestaende liefern den Betrag statt des negativen Werts."""
    document = {
        "nodes": [
            {
                "uid": "n1", "device_name": "Box", "device_mac_address": "AA:AA:AA:AA:AA:01",
                "mesh_role": "master", "is_meshed": True,
                "node_interfaces": [{
                    "uid": "i1", "type": "WLAN", "name": "WLAN 5GHz", "opmode": "AP_MODE",
                    "mac_address": "AA:AA:AA:AA:AA:01",
                    "node_links": [{
                        "type": "WLAN", "state": "CONNECTED",
                        "node_interface_1_uid": "i1", "node_interface_2_uid": "i2",
                        "rssi": 63,
                    }],
                }],
            },
            {
                "uid": "n2", "device_name": "TV", "device_mac_address": "BB:BB:BB:BB:BB:02",
                "mesh_role": "client",
                "node_interfaces": [{
                    "uid": "i2", "type": "WLAN", "name": "WLAN 5GHz", "opmode": "CLIENT_MODE",
                    "mac_address": "BB:BB:BB:BB:BB:02", "node_links": [],
                }],
            },
        ]
    }
    scan = parse_mesh_list(document, ts=1.0)
    assert scan.samples[0].rssi_dbm == -63.0


def test_unsinnige_rssi_werte_werden_ignoriert():
    document = {
        "nodes": [{
            "uid": "n1", "device_mac_address": "AA:AA:AA:AA:AA:01", "mesh_role": "master",
            "node_interfaces": [
                {
                    "uid": "i1", "type": "WLAN", "name": "WLAN 5GHz", "opmode": "AP_MODE",
                    "mac_address": "AA:AA:AA:AA:AA:01",
                    "node_links": [{
                        "type": "WLAN", "state": "CONNECTED",
                        "node_interface_1_uid": "i1", "node_interface_2_uid": "i2",
                        "rssi": -250,
                    }],
                },
                {
                    "uid": "i2", "type": "WLAN", "name": "WLAN 5GHz", "opmode": "CLIENT_MODE",
                    "mac_address": "BB:BB:BB:BB:BB:02", "node_links": [],
                },
            ],
        }]
    }
    assert parse_mesh_list(document, ts=1.0).samples[0].rssi_dbm is None


# --- Mesh-Schema 8.x (FRITZ!OS 8.2x) -------------------------------------- #


@pytest.fixture
def scan87(mesh_json_schema87):
    return parse_mesh_list(mesh_json_schema87, ts=1000.0)


def test_neues_schema_liefert_messwerte(scan87):
    """Ab FRITZ!OS 8.2x heisst die Feldstaerke rx_rcpi statt rssi. Ohne diese
    Felder kommt von einer 5690 Pro kein einziger Messwert an."""
    assert len(scan87.samples) == 3
    assert all(s.rssi_dbm is not None for s in scan87.samples)
    assert all(s.snr_db is not None for s in scan87.samples)


def test_rcpi_wird_als_feldstaerke_gelesen(scan87):
    flur = next(s for s in scan87.samples if s.peer_name == "Luftreiniger Flur")
    assert flur.rssi_dbm == -58.0
    assert flur.snr_db == 31.0


def test_nicht_messbare_richtung_wird_ignoriert(scan87):
    """tx_rcpi/tx_rsni stehen auf 255 - nach IEEE 802.11k heisst das
    'nicht messbar' und darf nicht als -255 dBm durchgehen."""
    assert all(-110 <= s.rssi_dbm <= -10 for s in scan87.samples)
    assert all(0 <= s.snr_db <= 100 for s in scan87.samples)


def test_band_wird_ueber_die_frequenz_bestimmt(scan87):
    """Das 6-GHz-Interface sendet auf Kanal 5. Ueber die Kanalnummer waere das
    2,4 GHz - nur die Mittenfrequenz ist eindeutig."""
    wohnzimmer = next(s for s in scan87.samples if s.peer_name == "Luftreiniger Wohnzimmer")
    assert wohnzimmer.band == BAND_6


def test_lan_strecken_werden_nicht_als_funkstrecke_gefuehrt(scan87):
    """Ein per LAN angebundener Repeater hat keine Funkstrecke zur Box -
    das ist kein Fehler, sondern der Normalfall bei Kabel-Backhaul."""
    assert not any(s.peer_name == "Repeater per LAN" for s in scan87.samples)
    # Seine Clients werden aber sehr wohl erfasst.
    kamera = next(s for s in scan87.samples if s.peer_name == "Kamera Eingang")
    assert kamera.ap_name == "Repeater per LAN"


def test_getrennte_verbindung_mit_255_wird_verworfen(scan87):
    assert not any(s.peer_name == "Altes Geraet" for s in scan87.samples)


def test_rcpi_im_rohformat_nach_norm():
    """Liefert eine Firmware RCPI im Rohformat (0..220), gilt dBm = RCPI/2-110."""
    dokument = {
        "nodes": [{
            "uid": "n1", "device_mac_address": "AA:AA:AA:AA:AA:01", "mesh_role": "master",
            "node_interfaces": [
                {"uid": "i1", "type": "WLAN", "name": "AP:5G:0", "opmode": "AP",
                 "mac_address": "AA:AA:AA:AA:AA:01",
                 "node_links": [{"type": "WLAN", "state": "CONNECTED",
                                 "node_interface_1_uid": "i1", "node_interface_2_uid": "i2",
                                 "rx_rcpi": 104}]},
                {"uid": "i2", "type": "WLAN", "name": "STA:5G:0", "opmode": "STA",
                 "mac_address": "BB:BB:BB:BB:BB:02", "node_links": []},
            ],
        }]
    }
    assert parse_mesh_list(dokument, ts=1.0).samples[0].rssi_dbm == -58.0
