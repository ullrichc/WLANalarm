"""Tests der REST-Schnittstelle gegen einen echt laufenden Server."""

import json
import urllib.error
import urllib.request

import pytest

from wlanalarm.engine import Engine
from wlanalarm.sources.synthetic import SyntheticSource
from wlanalarm.storage import Storage
from wlanalarm.web import WebServer

from conftest import make_config, quiet_links


@pytest.fixture
def dienst(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    storage = Storage(tmp_path)
    engine = Engine(make_config(), source, storage=storage,
                    clock=lambda: source.start_ts + source.elapsed)
    for _ in range(30):
        engine.tick()
    server = WebServer(engine, storage, "127.0.0.1", 0, token="")
    server.start()
    yield server, engine, storage
    server.stop()
    storage.close()


def hole(server, pfad, token=None, method="GET", body=None):
    url = server.address.rstrip("/") + pfad
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read() or b"null")


def test_status_liefert_den_zustand(dienst):
    server, _, _ = dienst
    status, payload = hole(server, "/api/status")
    assert status == 200
    assert payload["state"] == "disarmed"
    assert payload["link_count"] == 3
    assert len(payload["links"]) == 3


def test_health_meldet_gesunden_betrieb(dienst):
    server, _, _ = dienst
    status, payload = hole(server, "/api/health")
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["reasons"] == []


def test_health_erkennt_eine_stehende_messschleife(tmp_path):
    """Ein Gesundheitsendpunkt, der nur bestätigt, dass der Webserver läuft,
    ist für eine Alarmanlage schlimmer als keiner: Er meldet Betriebsbereit-
    schaft, während die Messung seit Stunden steht."""
    import urllib.error

    from wlanalarm.sources.base import SourceError

    class TodeQuelle(SyntheticSource):
        def scan(self):
            raise SourceError("FRITZ!Box nicht erreichbar")

    zeit = {"t": 1_700_000_000.0}
    source = TodeQuelle(quiet_links(), interval=2.0, seed=3)
    storage = Storage(tmp_path)
    engine = Engine(make_config(), source, storage=storage, clock=lambda: zeit["t"])
    for _ in range(10):
        engine.tick()
        zeit["t"] += 2.0

    server = WebServer(engine, storage, "127.0.0.1", 0, token="")
    server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as fehler:
            hole(server, "/api/health")
        assert fehler.value.code == 503
        payload = json.loads(fehler.value.read())
        assert payload["status"] == "degraded"
        assert any("Messfehler in Folge" in grund for grund in payload["reasons"])
    finally:
        server.stop()
        storage.close()


def test_dashboard_wird_ausgeliefert(dienst):
    server, _, _ = dienst
    with urllib.request.urlopen(server.address, timeout=5) as response:
        body = response.read().decode("utf-8")
    assert response.headers["Content-Type"].startswith("text/html")
    assert "<title>WLANalarm</title>" in body


def test_modus_laesst_sich_umschalten(dienst):
    server, engine, _ = dienst
    status, payload = hole(server, "/api/mode", method="POST", body={"mode": "armed_away"})
    assert status == 200
    assert payload["mode"] == "armed_away"
    assert engine.panel.mode == "armed_away"

    hole(server, "/api/mode", method="POST", body={"mode": "disarmed"})
    assert engine.panel.mode == "disarmed"


def test_unbekannter_modus_wird_abgelehnt(dienst):
    server, _, _ = dienst
    with pytest.raises(urllib.error.HTTPError) as fehler:
        hole(server, "/api/mode", method="POST", body={"mode": "armed_spaceship"})
    assert fehler.value.code == 400


def test_kaputtes_json_wird_abgelehnt(dienst):
    server, _, _ = dienst
    request = urllib.request.Request(
        server.address.rstrip("/") + "/api/mode", data=b"{kein json", method="POST"
    )
    request.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as fehler:
        urllib.request.urlopen(request, timeout=5)
    assert fehler.value.code == 400


def test_fremde_webseite_kann_die_anlage_nicht_entschaerfen(dienst):
    """Ohne diese Prüfung könnte eine beliebige Seite, die im Browser desselben
    Rechners geöffnet ist, per Formular-POST entschärfen - im vorgesehenen
    Betrieb auf 127.0.0.1 ganz ohne Token."""
    server, engine, _ = dienst
    engine.set_mode("armed_away", source="test")
    ziel = server.address.rstrip("/") + "/api/mode"

    # Ein einfaches Formular kann diesen Inhaltstyp nicht setzen.
    request = urllib.request.Request(
        ziel, data=b'{"mode":"disarmed"}', method="POST")
    request.add_header("Content-Type", "text/plain")
    with pytest.raises(urllib.error.HTTPError) as fehler:
        urllib.request.urlopen(request, timeout=5)
    assert fehler.value.code == 403

    # Auch ein passender Inhaltstyp hilft nicht, wenn die Herkunft fremd ist.
    request = urllib.request.Request(
        ziel, data=b'{"mode":"disarmed"}', method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Origin", "http://boese.example")
    with pytest.raises(urllib.error.HTTPError) as fehler:
        urllib.request.urlopen(request, timeout=5)
    assert fehler.value.code == 403

    assert engine.panel.mode == "armed_away", "Die Anlage wurde entschärft"


def test_das_eigene_dashboard_darf_weiterhin_schalten(dienst):
    server, engine, _ = dienst
    ziel = server.address.rstrip("/") + "/api/mode"
    request = urllib.request.Request(ziel, data=b'{"mode":"armed_night"}', method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Origin", server.address.rstrip("/"))
    with urllib.request.urlopen(request, timeout=5) as antwort:
        assert antwort.status == 200
    assert engine.panel.mode == "armed_night"


def test_unbekannter_pfad_ergibt_404(dienst):
    server, _, _ = dienst
    with pytest.raises(urllib.error.HTTPError) as fehler:
        hole(server, "/api/gibtsnicht")
    assert fehler.value.code == 404


def test_ereignisliste(dienst):
    server, engine, _ = dienst
    engine.set_mode("armed_night", source="test")
    _, events = hole(server, "/api/events?limit=5")
    assert any(event["type"] in ("arming", "armed") for event in events)


def test_ohne_token_kein_zugriff(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    storage = Storage(tmp_path)
    engine = Engine(make_config(), source, storage=storage,
                    clock=lambda: source.start_ts + source.elapsed)
    server = WebServer(engine, storage, "127.0.0.1", 0, token="s3cret")
    server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as fehler:
            hole(server, "/api/status")
        assert fehler.value.code == 401

        with pytest.raises(urllib.error.HTTPError):
            hole(server, "/api/status", token="falsch")

        status, _ = hole(server, "/api/status", token="s3cret")
        assert status == 200

        # Auch als Abfrageparameter, damit das Dashboard im Browser funktioniert.
        assert hole(server, "/api/status?token=s3cret")[0] == 200

        # Der Health-Endpunkt bleibt frei, damit Monitoring funktioniert.
        assert hole(server, "/api/health")[0] == 200
    finally:
        server.stop()
        storage.close()


def test_schalten_ohne_token_wird_abgewiesen(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    storage = Storage(tmp_path)
    engine = Engine(make_config(), source, storage=storage,
                    clock=lambda: source.start_ts + source.elapsed)
    server = WebServer(engine, storage, "127.0.0.1", 0, token="s3cret")
    server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as fehler:
            hole(server, "/api/mode", method="POST", body={"mode": "disarmed"})
        assert fehler.value.code == 401
        assert engine.panel.mode == "disarmed"
    finally:
        server.stop()
        storage.close()


def test_dashboard_erklaert_seine_zahlen(dienst):
    """Ein Dashboard, das nur Zahlen hinwirft, ist wertlos - die Begriffe
    'Ausschlag' und 'Abweichung' versteht niemand ohne Erklärung."""
    server, _, _ = dienst
    with urllib.request.urlopen(server.address, timeout=5) as response:
        seite = response.read().decode("utf-8")

    for begriff in ("Was bedeuten diese Zahlen?", "Ausschlag", "Abweichung",
                    "Empfangsstärke in dBm", "Auslöseschwelle", "unruhig"):
        assert begriff in seite, f"{begriff!r} fehlt im Dashboard"

    # Jede Betriebsart trägt eine sichtbare Erklärung, nicht bloß einen
    # Tooltip - den bekommt auf einem Mobilgerät niemand zu sehen.
    import re

    bereich = seite[seite.index('class="modes"'):seite.index('id="modes-note"')]
    knoepfe = re.findall(r'data-mode="(\w+)"', bereich)
    assert set(knoepfe) == {"disarmed", "armed_home", "armed_night", "armed_away"}
    assert bereich.count("<small>") == len(knoepfe)


def test_dashboard_erklaert_den_laufenden_vorgang(dienst):
    """Ohne diese Erklärung bleibt offen, ob eine ruhige Anzeige bedeutet,
    dass alles in Ordnung ist - oder dass gar nichts gemessen wird."""
    server, _, _ = dienst
    with urllib.request.urlopen(server.address, timeout=5) as response:
        seite = response.read().decode("utf-8")

    assert "Was gerade geschieht" in seite
    assert "beschreibeVorgang" in seite
    for baustein in ("Aufwärmphase", "Eingangsverzögerung", "Ausgangsverzögerung",
                     "unscharf ist, führt eine Bewegung zu keinem Alarm"):
        assert baustein in seite, f"{baustein!r} fehlt in der Vorgangsbeschreibung"


def test_dashboard_zeigt_die_verworfenen_geraete(dienst):
    """Dieselbe Auskunft wie 'wlanalarm discover' - warum ein Gerät nicht
    als Bewegungsmelder dient -, nur ohne Kommandozeile."""
    server, _, _ = dienst
    with urllib.request.urlopen(server.address, timeout=5) as response:
        seite = response.read().decode("utf-8")

    assert "nicht als Bewegungsmelder verwendet" in seite
    assert "renderUnused" in seite

    # Die Daten dafür liefert der Zustand bereits mit.
    _, status = hole(server, "/api/status")
    assert "candidates" in status
    assert all("reason" in c and "selected" in c for c in status["candidates"])


def test_status_liefert_die_schwellen_fuer_die_anzeige(dienst):
    """Ohne sie kann das Dashboard nicht sagen, ab wann ein Wert auffällig ist."""
    server, _, _ = dienst
    _, status = hole(server, "/api/status")
    assert 0 < status["trigger_score"] <= 1
    assert status["min_links"] >= 1
    assert status["warmup_samples"] >= 5
