"""Tests der FRITZ!Box-Anbindung gegen eine nachgebaute TR-064-Schnittstelle."""

import json
from pathlib import Path

import pytest

from wlanalarm.model import BAND_5, BAND_24
from wlanalarm.sources.base import SourceError
from wlanalarm.sources.fritz_mesh import FritzMeshSource
from wlanalarm.sources.fritz_tr064 import FritzTr064Source, _band_from_info

DATA = Path(__file__).parent / "data"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self._status = status_code
        self.aufgerufen = []

    def get(self, url, timeout=None):
        self.aufgerufen.append(url)
        return FakeResponse(self._payload, self._status)

    def close(self):
        pass


class FakeService:
    def __init__(self, actions):
        self.actions = actions


class FakeConnection:
    """Minimaler Nachbau von fritzconnection.FritzConnection."""

    def __init__(self, mesh_payload=None, status_code=200, actions=None, antworten=None):
        self.address = "http://fritz.box"
        self.port = 49000
        self.session = FakeSession(mesh_payload, status_code)
        self.services = {
            "Hosts1": FakeService(actions if actions is not None else ["X_AVM-DE_GetMeshListPath"])
        }
        self._antworten = antworten or {}
        self.aufrufe = []

    def call_action(self, service, action, **kwargs):
        self.aufrufe.append((service, action, kwargs))
        key = (service, action)
        if key in self._antworten:
            wert = self._antworten[key]
            return wert(**kwargs) if callable(wert) else wert
        if action == "GetInfo" and service == "DeviceInfo:1":
            return {"NewModelName": "FRITZ!Box 5690 Pro", "NewSoftwareVersion": "8.20"}
        if action == "X_AVM-DE_GetMeshListPath":
            return {"NewX_AVM-DE_MeshListPath": "/mesh.json?sid=abc"}
        raise KeyError(f"unerwarteter Aufruf: {service}.{action}")


@pytest.fixture
def mesh_payload():
    return json.loads((DATA / "mesh_sample.json").read_text(encoding="utf-8"))


def mesh_source(connection) -> FritzMeshSource:
    source = FritzMeshSource("http://fritz.box", 49000, "u", "p")
    source._connection = connection
    return source


def test_mesh_quelle_liefert_messwerte(mesh_payload):
    connection = FakeConnection(mesh_payload)
    scan = mesh_source(connection).scan()
    assert len(scan.samples) == 5
    assert connection.session.aufgerufen == ["http://fritz.box:49000/mesh.json?sid=abc"]


def test_mesh_quelle_meldet_fehlende_berechtigung(mesh_payload):
    source = mesh_source(FakeConnection(mesh_payload, status_code=403))
    with pytest.raises(SourceError, match="keinen Zugriff"):
        source.scan()


def test_check_erkennt_boxen_ohne_mesh_liste(mesh_payload):
    source = mesh_source(FakeConnection(mesh_payload, actions=[]))
    with pytest.raises(SourceError, match="keine Mesh-Liste"):
        source.check()


def test_check_liefert_modell_und_firmware(mesh_payload):
    assert mesh_source(FakeConnection(mesh_payload)).check() == (
        "FRITZ!Box 5690 Pro, FRITZ!OS 8.20"
    )


def test_verbindung_wird_nach_einem_fehler_verworfen(mesh_payload):
    class KaputteVerbindung(FakeConnection):
        def call_action(self, service, action, **kwargs):
            if action == "X_AVM-DE_GetMeshListPath":
                raise RuntimeError("Zeitüberschreitung")
            return super().call_action(service, action, **kwargs)

    source = mesh_source(KaputteVerbindung(mesh_payload))
    with pytest.raises(SourceError):
        source.scan()
    # Beim naechsten Versuch wird neu angemeldet, statt eine tote Sitzung
    # weiterzuverwenden.
    assert source._connection is None


# -- TR-064-Fallback ------------------------------------------------------- #


def tr064_source(antworten) -> FritzTr064Source:
    connection = FakeConnection(antworten=antworten)
    connection.services["WLANConfiguration1"] = FakeService([])
    connection.services["WLANConfiguration2"] = FakeService([])
    source = FritzTr064Source("http://fritz.box", 49000, "u", "p")
    source._connection = connection
    return source


def test_tr064_liest_signalstaerke_in_prozent():
    geraet = {
        "NewAssociatedDeviceMACAddress": "AA:BB:CC:00:00:03",
        "NewAssociatedDeviceIPAddress": "192.168.178.30",
        "NewAssociatedDeviceAuthState": True,
        "NewX_AVM-DE_SignalStrength": 72,
        "NewX_AVM-DE_Speed": 866,
    }
    source = tr064_source({
        ("WLANConfiguration:1", "GetInfo"): {"NewEnable": True, "NewChannel": 6,
                                             "NewStandard": "n", "NewBSSID": "34:31:C4:00:00:04"},
        ("WLANConfiguration:2", "GetInfo"): {"NewEnable": True, "NewChannel": 100,
                                             "NewStandard": "ax", "NewBSSID": "34:31:C4:00:00:05"},
        ("WLANConfiguration:1", "GetTotalAssociations"): {"NewTotalAssociations": 0},
        ("WLANConfiguration:2", "GetTotalAssociations"): {"NewTotalAssociations": 1},
        ("WLANConfiguration:2", "GetGenericAssociatedDeviceInfo"): geraet,
    })
    scan = source.scan()
    assert len(scan.samples) == 1
    sample = scan.samples[0]
    assert sample.signal_percent == 72.0
    assert sample.rssi_dbm is None          # dBm gibt es hier nicht
    assert sample.rx_rate_kbps == 866000.0
    assert sample.band == BAND_5


def test_tr064_ueberspringt_nicht_angemeldete_geraete():
    source = tr064_source({
        ("WLANConfiguration:1", "GetInfo"): {"NewEnable": True, "NewChannel": 6,
                                             "NewStandard": "n", "NewBSSID": "AA:AA:AA:AA:AA:AA"},
        ("WLANConfiguration:2", "GetInfo"): {"NewEnable": False},
        ("WLANConfiguration:1", "GetTotalAssociations"): {"NewTotalAssociations": 1},
        ("WLANConfiguration:1", "GetGenericAssociatedDeviceInfo"): {
            "NewAssociatedDeviceMACAddress": "AA:BB:CC:00:00:03",
            "NewAssociatedDeviceAuthState": False,
        },
    })
    assert source.scan().samples == []


def test_ohne_aktives_wlan_gibt_es_einen_klaren_fehler():
    source = tr064_source({
        ("WLANConfiguration:1", "GetInfo"): {"NewEnable": False},
        ("WLANConfiguration:2", "GetInfo"): {"NewEnable": False},
    })
    with pytest.raises(SourceError, match="Keine aktive"):
        source.scan()


@pytest.mark.parametrize(
    "info, erwartet",
    [
        ({"NewStandard": "n", "NewChannel": 6}, BAND_24),
        ({"NewStandard": "ax", "NewChannel": 100}, BAND_5),
        ({"NewStandard": "g", "NewChannel": None}, BAND_24),
    ],
)
def test_bandbestimmung_aus_getinfo(info, erwartet):
    assert _band_from_info(info) == erwartet


# -- Fehlermeldungen ------------------------------------------------------- #


@pytest.mark.parametrize(
    "meldung, erwartet",
    [
        ("Connection to fritz.box timed out. (connect timeout=10.0)", "antwortet nicht"),
        ("Max retries exceeded with url: /igddesc.xml", "antwortet nicht"),
        ("401 Unauthorized", "Anmeldung zurueck"),
        ("Name or service not known", "laesst sich nicht aufloesen"),
        ("irgendwas ganz anderes", "fehlgeschlagen"),
    ],
)
def test_verbindungsfehler_werden_uebersetzt(meldung, erwartet):
    """Die Rohmeldungen von fritzconnection sind für Anwender unbrauchbar."""
    from wlanalarm.sources.fritz_base import _erklaere

    text = _erklaere(RuntimeError(meldung), "http://fritz.box", 49000)
    assert erwartet in text


def test_hinweis_auf_die_freizugebende_einstellung():
    from wlanalarm.sources.fritz_base import _erklaere

    text = _erklaere(RuntimeError("timed out"), "http://fritz.box", 49000)
    assert "Zugriff fuer Anwendungen zulassen" in text


def test_beide_quellen_teilen_sich_die_verbindungslogik():
    from wlanalarm.sources.fritz_base import FritzSource

    assert issubclass(FritzMeshSource, FritzSource)
    assert issubclass(FritzTr064Source, FritzSource)
