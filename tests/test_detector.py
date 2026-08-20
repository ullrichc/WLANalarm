"""Verhaltenstests des Detektors gegen synthetische Messreihen."""

from wlanalarm.detector import MotionDetector
from wlanalarm.sources.synthetic import SyntheticLink, SyntheticSource

from conftest import make_config, quiet_links, run_source


def test_ruhige_wohnung_loest_nicht_aus():
    config = make_config()
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    results = run_source(MotionDetector(config), source, 900, config)
    assert not any(result.motion for result in results)


def test_bewegung_wird_erkannt():
    config = make_config()
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(600, 660)], seed=3)
    results = run_source(MotionDetector(config), source, 500, config)
    bewegt = [r for r in results if r.motion]
    assert bewegt, "Bewegung wurde gar nicht erkannt"
    erste = next(r for r in results if r.motion)
    # Innerhalb weniger Sekunden nach Beginn der Bewegung.
    assert 600 <= erste.ts - source.start_ts <= 615


def test_einzelnes_zickiges_geraet_loest_keinen_alarm_aus():
    """Der wichtigste Schutz gegen Fehlalarme: eine einzelne Strecke, die
    Aerger macht, wird von den ruhigen Nachbarstrecken widerlegt."""
    config = make_config()
    source = SyntheticSource(quiet_links(), interval=2.0, motion_windows=[(600, 800)],
                             motion_links=["AA:BB:CC:00:00:03"], seed=11)
    results = run_source(MotionDetector(config), source, 500, config)
    assert not any(result.motion for result in results)


def test_einzelne_strecke_zaehlt_wenn_es_keine_zweite_gibt():
    """In einer kleinen Wohnung mit nur einem stationaeren Geraet muss die
    Erkennung trotzdem funktionieren."""
    config = make_config()
    links = quiet_links()[:1]
    source = SyntheticSource(links, interval=2.0, motion_windows=[(600, 700)], seed=3)
    results = run_source(MotionDetector(config), source, 400, config)
    assert any(result.motion for result in results)


def test_allow_single_strong_erlaubt_den_einzelausschlag():
    config = make_config(detector={"allow_single_strong": True})
    source = SyntheticSource(quiet_links(), interval=2.0, motion_windows=[(600, 800)],
                             motion_links=["AA:BB:CC:00:00:03"], seed=11)
    results = run_source(MotionDetector(config), source, 500, config)
    assert any(result.motion for result in results)


def test_waehrend_der_aufwaermphase_wird_nichts_gemeldet():
    config = make_config(sampling={"warmup_samples": 50})
    source = SyntheticSource(quiet_links(), interval=2.0, motion_windows=[(0, 200)], seed=3)
    results = run_source(MotionDetector(config), source, 40, config)
    assert not any(result.motion for result in results)
    assert not any(result.ready for result in results)


def test_dauerhaft_unruhige_strecke_wird_als_ungeeignet_markiert():
    config = make_config()
    links = quiet_links() + [
        SyntheticLink("Kaputte Kamera", "AA:BB:CC:00:00:09",
                      base_rssi=-75, quiet_noise_db=8.0, motion_noise_db=8.0)
    ]
    source = SyntheticSource(links, interval=2.0, seed=3)
    results = run_source(MotionDetector(config), source, 600, config)
    letzte = results[-1]
    kaputt = next(l for l in letzte.links if l.peer_name == "Kaputte Kamera")
    assert kaputt.unstable
    assert kaputt.score == 0.0
    assert not letzte.motion


def test_baseline_lernt_bewegung_nicht_als_normalzustand():
    """Bei anhaltender Bewegung darf die Ruhe-Baseline nicht nachziehen -
    sonst meldet die Anlage nach einigen Minuten nichts mehr."""
    config = make_config()
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(400, 3000)], seed=3)
    detector = MotionDetector(config)
    results = run_source(detector, source, 900, config)
    spaet = [r for r in results[-100:] if r.motion]
    assert len(spaet) > 90, "Die Bewegung wurde nach einiger Zeit nicht mehr erkannt"


def test_gewicht_null_nimmt_eine_strecke_aus_der_wertung():
    config = make_config(
        links=[{"mac": "AA:BB:CC:00:00:03", "weight": 0.0}],
        detector={"min_links": 1},
    )
    source = SyntheticSource(quiet_links(), interval=2.0, motion_windows=[(600, 800)],
                             motion_links=["AA:BB:CC:00:00:03"], seed=11)
    results = run_source(MotionDetector(config), source, 500, config)
    assert not any(result.motion for result in results)


def test_zonen_werden_im_ergebnis_gefuehrt():
    config = make_config(
        links=[
            {"mac": "34:31:C4:00:00:20", "zone": "flur"},
            {"mac": "AA:BB:CC:00:00:03", "zone": "wohnzimmer"},
            {"mac": "AA:BB:CC:00:00:04", "zone": "wohnzimmer"},
        ]
    )
    source = SyntheticSource(quiet_links(), interval=2.0, motion_windows=[(600, 660)], seed=3)
    results = run_source(MotionDetector(config), source, 400, config)
    bewegt = next(r for r in results if r.motion)
    assert set(bewegt.zones) <= {"flur", "wohnzimmer"}
    assert bewegt.zones


def test_verschwundene_strecke_wird_vergessen():
    config = make_config(detector={"stale_after_seconds": 10.0})
    detector = MotionDetector(config)
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    for _ in range(20):
        scan = source.scan()
        detector.update(scan)
    assert len(detector.trackers) == 3

    # Ab jetzt taucht nur noch eine Strecke auf.
    for _ in range(20):
        scan = source.scan()
        scan.samples = scan.samples[:1]
        detector.update(scan)
    assert len(detector.trackers) == 1


def test_baselines_lassen_sich_sichern_und_wiederherstellen():
    config = make_config()
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    detector = MotionDetector(config)
    run_source(detector, source, 400, config)
    snapshots = detector.export_baselines()
    assert len(snapshots) == 3

    frisch = MotionDetector(config)
    assert frisch.seed_baselines(snapshots) == 0  # noch keine Tracker angelegt
    run_source(frisch, SyntheticSource(quiet_links(), interval=2.0, seed=4), 10, config)
    # Nach dem ersten Scan greifen die geladenen Werte.
    assert all(t.baseline.ready for t in frisch.trackers.values())


def test_prozentwerte_aus_tr064_werden_ausgewertet():
    """Beim TR-064-Fallback gibt es keinen dBm-Wert, nur Prozent."""
    from wlanalarm.detector import _signal_db
    from wlanalarm.model import LinkSample

    mit_dbm = LinkSample(ts=0, link_id="x", ap_mac="", ap_name="", peer_mac="",
                         peer_name="", rssi_dbm=-55.0, signal_percent=70.0)
    nur_prozent = LinkSample(ts=0, link_id="x", ap_mac="", ap_name="", peer_mac="",
                             peer_name="", signal_percent=70.0)
    ohne = LinkSample(ts=0, link_id="x", ap_mac="", ap_name="", peer_mac="", peer_name="")

    assert _signal_db(mit_dbm) == -55.0
    assert _signal_db(nur_prozent) == -51.0
    assert _signal_db(ohne) is None


def test_perfekt_konstantes_signal_liefert_eine_baseline():
    """Regression: ein Aktivitätswert von exakt 0 ist eine gültige Messung -
    die beste überhaupt - und darf nicht als 'keine Daten' gelten."""
    from wlanalarm.model import LinkSample, Scan

    config = make_config()
    detector = MotionDetector(config)
    # Genug Messpunkte, damit die Baseline aus eigener Kraft belastbar wird.
    for i in range(120):
        sample = LinkSample(
            ts=1000.0 + i * 2, link_id="l1", ap_mac="AA:AA:AA:AA:AA:01", ap_name="Box",
            peer_mac="BB:BB:BB:BB:BB:02", peer_name="TV", band="5 GHz",
            rssi_dbm=-55.0, rx_rate_kbps=866000.0,
        )
        result = detector.update(Scan(ts=sample.ts, samples=[sample]))

    link = result.links[0]
    assert link.activity_db == 0.0
    assert link.ready
    baselines = detector.export_baselines()
    assert baselines["l1"].median == 0.0
    assert not result.motion


def test_zu_kurze_messreihe_liefert_keine_aktivitaet():
    from wlanalarm.model import LinkSample, Scan

    detector = MotionDetector(make_config())
    sample = LinkSample(ts=1000.0, link_id="l1", ap_mac="AA:AA:AA:AA:AA:01", ap_name="Box",
                        peer_mac="BB:BB:BB:BB:BB:02", peer_name="TV", rssi_dbm=-55.0)
    result = detector.update(Scan(ts=1000.0, samples=[sample]))
    assert result.links[0].activity_db is None
    assert result.links[0].to_dict()["activity_db"] is None
