from wlanalarm.selection import evaluate, selected_ids
from wlanalarm.sources.mesh_parser import parse_mesh_list

from conftest import make_config


def scan_of(mesh_json):
    return parse_mesh_list(mesh_json, ts=1000.0)


def namen(candidates, selected=True):
    return {c.sample.peer_name for c in candidates if c.selected is selected}


def test_alle_stationaeren_geraete_werden_gewaehlt(mesh_json):
    gewaehlt = namen(evaluate(scan_of(mesh_json), make_config()))
    assert gewaehlt == {
        "Repeater Flur", "Fernseher", "Sonos Wohnzimmer",
        "Kamera Eingang", "Steckdose Kueche",
    }


def test_24_ghz_wird_nicht_ausgeschlossen(mesh_json):
    """In vielen Haushalten haengen ausgerechnet die brauchbaren Dauerlaeufer -
    Steckdosen, Luftreiniger, Sensoren - nur im 2,4-GHz-Netz. Sie auszusperren
    kostet mehr, als das empfindlichere Band einbringt."""
    steckdose = next(
        c for c in evaluate(scan_of(mesh_json), make_config())
        if c.sample.peer_name == "Steckdose Kueche"
    )
    assert steckdose.selected


def test_bandfilter_laesst_sich_weiterhin_setzen(mesh_json):
    config = make_config(selection={"bands": ["6 GHz"], "always_use_mesh_nodes": False})
    assert namen(evaluate(scan_of(mesh_json), config)) == {"Sonos Wohnzimmer"}


def test_5_und_6_ghz_haben_vorrang_wenn_gekappt_wird(mesh_json):
    """Die Bandvorliebe steckt in der Rangfolge, nicht im Filter: Wird die Zahl
    der Strecken begrenzt, fliegt 2,4 GHz zuerst raus."""
    config = make_config(selection={"max_links": 3, "always_use_mesh_nodes": False})
    gewaehlt = namen(evaluate(scan_of(mesh_json), config))
    assert "Steckdose Kueche" not in gewaehlt


def test_konfiguriertes_geraet_umgeht_den_bandfilter(mesh_json):
    config = make_config(links=[{"mac": "AA:BB:CC:00:00:05", "zone": "kueche"}])
    candidates = evaluate(scan_of(mesh_json), config)
    steckdose = next(c for c in candidates if c.sample.peer_name == "Steckdose Kueche")
    assert steckdose.selected
    assert steckdose.zone == "kueche"


def test_mobile_geraete_werden_am_namen_erkannt(mesh_json):
    mesh_json["nodes"][2]["device_name"] = "Alex-iPhone"
    candidates = evaluate(scan_of(mesh_json), make_config())
    handy = next(c for c in candidates if c.sample.peer_name == "Alex-iPhone")
    assert not handy.selected
    assert "iphone" in handy.reason


def test_ausdrueckliche_konfiguration_schlaegt_die_namensheuristik(mesh_json):
    """Wer ein Tablet fest an die Wand haengt, darf es trotzdem verwenden."""
    mesh_json["nodes"][2]["device_name"] = "Wand-Tablet Android"
    config = make_config(links=[{"mac": "AA:BB:CC:00:00:03", "zone": "flur"}])
    candidates = evaluate(scan_of(mesh_json), config)
    assert next(c for c in candidates if c.sample.peer_mac == "AA:BB:CC:00:00:03").selected


def test_ignore_schliesst_ein_geraet_aus(mesh_json):
    config = make_config(links=[{"mac": "AA:BB:CC:00:00:03", "ignore": True}])
    candidates = evaluate(scan_of(mesh_json), config)
    assert not next(c for c in candidates if c.sample.peer_name == "Fernseher").selected


def test_sperrliste_wirkt(mesh_json):
    config = make_config(selection={"ignore_macs": ["aa-bb-cc-00-00-04"]})
    assert "Sonos Wohnzimmer" not in namen(evaluate(scan_of(mesh_json), config))


def test_only_configured_beschraenkt_auf_die_liste(mesh_json):
    config = make_config(
        selection={"only_configured": True},
        links=[{"mac": "AA:BB:CC:00:00:03"}],
    )
    gewaehlt = namen(evaluate(scan_of(mesh_json), config))
    # Der Mesh-Repeater kommt weiterhin dazu, er ist immer brauchbar.
    assert gewaehlt == {"Fernseher", "Repeater Flur"}


def test_max_links_kappt_die_schwaechsten(mesh_json):
    config = make_config(selection={"max_links": 2})
    candidates = evaluate(scan_of(mesh_json), config)
    gewaehlt = [c for c in candidates if c.selected]
    assert len(gewaehlt) == 2
    # Der Mesh-Knoten hat Vorrang.
    assert any(c.sample.peer_is_mesh_node for c in gewaehlt)
    assert any("max_links" in c.reason for c in candidates if not c.selected)


def test_mesh_knoten_kann_abgeschaltet_werden(mesh_json):
    config = make_config(selection={"always_use_mesh_nodes": False, "bands": ["6 GHz"]})
    assert "Repeater Flur" not in namen(evaluate(scan_of(mesh_json), config))


def test_selected_ids_passt_zur_auswahl(mesh_json):
    candidates = evaluate(scan_of(mesh_json), make_config())
    assert selected_ids(candidates) == {c.link_id for c in candidates if c.selected}


# --- Regression: typische Gerätelandschaft eines Haushalts ----------------- #
#
# Nachgebildet nach einem Praxisfall, in dem die Auswahl genau verkehrt herum
# arbeitete: Handy und Tablet wurden als Sensor verwendet, während die
# stationären Dauerläufer wegen des Bandfilters herausfielen.
#
# Die MAC-Adressen stammen aus dem für Dokumentation vorgesehenen Bereich
# 02:00:00:… (lokal verwaltet, keinem Hersteller zugeteilt).

BEISPIELGERAETE = [
    ("S24-von-Alex",            "02:00:00:00:01:01", "5 GHz",   -61, False),
    ("Tablet-Pad-5",            "02:00:00:00:01:02", "5 GHz",   -49, False),
    ("Pixel-9-Pro",             "02:00:00:00:01:03", "5 GHz",   -49, False),
    ("02:00:00:00:02:01",       "02:00:00:00:02:01", "2.4 GHz", -31, True),
    ("lwip0",                   "02:00:00:00:02:02", "2.4 GHz", -64, True),
    ("luftreiniger-wohnzimmer", "02:00:00:00:02:03", "2.4 GHz", -61, True),
    ("luftreiniger-flur",       "02:00:00:00:02:04", "2.4 GHz", -33, True),
]


def beispiel_scan():
    from wlanalarm.model import LinkSample, Scan, make_link_id

    ap = "34:31:C4:00:00:05"
    return Scan(ts=1000.0, samples=[
        LinkSample(
            ts=1000.0, link_id=make_link_id(ap, mac, band), ap_mac=ap,
            ap_name="fritz.box", peer_mac=mac, peer_name=name, band=band,
            rssi_dbm=float(rssi), snr_db=30.0, rx_rate_kbps=100000.0,
        )
        for name, mac, band, rssi, _ in BEISPIELGERAETE
    ])


def test_haushalt_waehlt_die_dauerlaeufer():
    candidates = {c.sample.peer_name: c for c in evaluate(beispiel_scan(), make_config())}
    for name, _, _, _, erwartet in BEISPIELGERAETE:
        assert candidates[name].selected is erwartet, (
            f"{name}: erwartet selected={erwartet}, "
            f"tatsächlich {candidates[name].selected} ({candidates[name].reason})"
        )


def test_persoenlich_benanntes_geraet_wird_erkannt():
    """Ein Name wie 'S24-von-Alex' passt auf keine Modellbezeichnung - aber wer
    sein Gerät nach sich benennt, trägt es auch mit sich herum."""
    candidates = {c.sample.peer_name: c for c in evaluate(beispiel_scan(), make_config())}
    assert "Person" in candidates["S24-von-Alex"].reason


def test_geraet_ohne_namen_wird_nicht_vorschnell_verworfen():
    """Eine reine MAC-Adresse als Name sagt nichts aus - so ein Gerät bekommt
    seine Chance und wird erst von der Kalibrierung beurteilt."""
    candidates = {c.sample.peer_name: c for c in evaluate(beispiel_scan(), make_config())}
    assert candidates["02:00:00:00:02:01"].selected
