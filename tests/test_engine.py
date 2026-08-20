"""End-to-End-Tests der Hauptschleife gegen synthetische Messreihen."""

import threading

from wlanalarm.alarm import STATE_ALARM, STATE_DISARMED
from wlanalarm.engine import Engine
from wlanalarm.sources.synthetic import SyntheticSource
from wlanalarm.storage import Storage

from conftest import make_config, quiet_links


class SammelHub:
    """Ersatz fuer den Benachrichtigungsverteiler, der nur mitschreibt."""

    def __init__(self):
        self.events = []
        self.states = []
        self.notifiers = []

    def dispatch(self, event):
        self.events.append(event)

    def publish_state(self, state):
        self.states.append(state)

    def close(self, timeout=None):
        pass

    def typen(self):
        return [event.type for event in self.events]


def baue_engine(tmp_path, source, **overrides):
    config = make_config(**overrides)
    storage = Storage(tmp_path)
    hub = SammelHub()
    # Die Engine soll die Zeit der Messreihe verwenden, nicht die Wanduhr.
    clock = lambda: source.start_ts + source.elapsed  # noqa: E731
    engine = Engine(config, source, storage=storage, hub=hub, clock=clock)
    return engine, storage, hub


def test_ruhige_wohnung_loest_keinen_alarm_aus(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    engine, storage, hub = baue_engine(
        tmp_path, source, alarm={"initial_mode": "armed_away", "exit_delay": 0.0}
    )
    for _ in range(600):
        engine.tick()
    assert engine.panel.state == "armed_away"
    assert "alarm" not in hub.typen()
    storage.close()


def test_einbruch_fuehrt_zu_alarm_und_wird_gespeichert(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(800, 900)], seed=3)
    engine, storage, hub = baue_engine(
        tmp_path, source,
        alarm={"initial_mode": "armed_away", "exit_delay": 0.0, "entry_delay": 20.0},
    )
    for _ in range(600):
        engine.tick()

    assert engine.panel.state == STATE_ALARM
    assert "pending" in hub.typen()
    assert "alarm" in hub.typen()

    gespeichert = [e["type"] for e in storage.recent_events()]
    assert "alarm" in gespeichert
    storage.close()


def test_entschaerfen_waehrend_der_eingangsverzoegerung_verhindert_den_alarm(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(800, 900)], seed=3)
    engine, storage, hub = baue_engine(
        tmp_path, source,
        alarm={"initial_mode": "armed_away", "exit_delay": 0.0, "entry_delay": 60.0},
    )
    entschaerft = False
    for _ in range(600):
        engine.tick()
        if engine.panel.state == "pending" and not entschaerft:
            engine.set_mode(STATE_DISARMED, source="test")
            entschaerft = True

    assert entschaerft, "Die Eingangsverzoegerung wurde nie erreicht"
    assert engine.panel.state == STATE_DISARMED
    assert "alarm" not in hub.typen()
    storage.close()


def test_modus_ueberlebt_einen_neustart(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    engine, storage, _ = baue_engine(tmp_path, source)
    engine.set_mode("armed_night", source="test")
    engine.shutdown()
    storage.close()

    source2 = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    engine2, storage2, _ = baue_engine(tmp_path, source2)
    assert engine2.panel.mode == "armed_night"
    storage2.close()


def test_kalibrierte_baselines_werden_beim_start_geladen(tmp_path, caplog):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    engine, storage, _ = baue_engine(tmp_path, source)
    for _ in range(400):
        engine.tick()
    engine.shutdown()
    gespeichert = storage.load_baselines()
    storage.close()
    assert len(gespeichert) == 3

    source2 = SyntheticSource(quiet_links(), interval=2.0, seed=9)
    engine2, storage2, _ = baue_engine(tmp_path, source2)
    engine2.tick()
    # Ohne geladene Baselines waere nach einem einzigen Tick nichts bereit.
    assert all(t.baseline.ready for t in engine2.detector.trackers.values())
    storage2.close()


def test_messfehler_werden_gezaehlt_und_beenden_den_dienst_nicht(tmp_path):
    from wlanalarm.sources.base import SourceError

    class KaputteQuelle(SyntheticSource):
        def scan(self):
            if self._tick % 3 == 1:
                self._tick += 1
                raise SourceError("FRITZ!Box antwortet nicht")
            return super().scan()

    source = KaputteQuelle(quiet_links(), interval=2.0, seed=3)
    engine, storage, _ = baue_engine(tmp_path, source)
    for _ in range(60):
        engine.tick()

    assert engine.stats.errors > 0
    assert engine.stats.ticks > 0
    assert engine.stats.consecutive_errors == 0  # der letzte Versuch klappte wieder
    assert "antwortet nicht" in engine.stats.last_error
    storage.close()


def test_snapshot_enthaelt_alles_fuers_dashboard(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    engine, storage, _ = baue_engine(tmp_path, source)
    for _ in range(30):
        engine.tick()

    snapshot = engine.snapshot()
    assert snapshot["state"] == STATE_DISARMED
    assert snapshot["link_count"] == 3
    assert len(snapshot["links"]) == 3
    assert len(snapshot["candidates"]) == 3
    assert len(snapshot["history"]) == 30
    assert snapshot["source"] == "synthetic"
    storage.close()


def test_run_beendet_sich_nach_max_ticks(tmp_path):
    source = SyntheticSource(quiet_links(), interval=0.001, seed=3)
    engine, storage, _ = baue_engine(tmp_path, source, sampling={
        "interval": 0.5, "window_seconds": 12.0, "baseline_seconds": 900.0
    })
    stop = threading.Event()
    engine.run(stop_event=stop, max_ticks=3)
    assert engine.stats.ticks == 3
    storage.close()


def test_bewegungsmeldung_bleibt_im_unscharfen_zustand_stumm(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(600, 700)], seed=3)
    engine, storage, hub = baue_engine(tmp_path, source)
    for _ in range(500):
        engine.tick()
    assert "motion" not in hub.typen()
    storage.close()


def test_bewegungsmeldung_kann_auch_unscharf_gewuenscht_sein(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(600, 700)], seed=3)
    engine, storage, hub = baue_engine(
        tmp_path, source, alarm={"notify_motion_when_disarmed": True}
    )
    for _ in range(500):
        engine.tick()
    assert "motion" in hub.typen()
    storage.close()


def test_aufzeichnung_enthaelt_nur_die_sensorstrecken(tmp_path):
    """Der ungefilterte Scan enthält auch die aussortierten Handys. Ein
    Mitschnitt davon wäre ein Sekundenprotokoll darüber, wer wann zu Hause
    war - genau die Datenart, vor deren heimlicher Erhebung die eigene
    Datenschutzdokumentation warnt."""
    from wlanalarm.recorder import Recorder
    from wlanalarm.sources.replay import iter_recording
    from wlanalarm.sources.synthetic import SyntheticLink

    links = quiet_links() + [
        SyntheticLink("Handy-von-Alex", "02:00:00:00:01:01", base_rssi=-60,
                      quiet_noise_db=1.0),
    ]
    source = SyntheticSource(links, interval=2.0, seed=3)
    config = make_config()
    storage = Storage(tmp_path)
    recorder = Recorder(tmp_path / "rec")
    engine = Engine(config, source, storage=storage, recorder=recorder,
                    clock=lambda: source.start_ts + source.elapsed)
    for _ in range(20):
        engine.tick()
    recorder.close()

    datei = next((tmp_path / "rec").glob("samples-*.ndjson"))
    namen = {s.peer_name for scan in iter_recording(datei) for s in scan.samples}
    assert "Handy-von-Alex" not in namen, "Aussortiertes Gerät wurde mitgeschnitten"
    assert "Fernseher" in namen
    storage.close()


def test_zustand_enthaelt_zonen_fuer_home_assistant(tmp_path):
    """Die dokumentierte Automatisierung prüft state_attr(..., 'zones').
    Ohne dieses Feld läuft sie in einen Template-Fehler und feuert nie."""
    source = SyntheticSource(quiet_links(), interval=2.0,
                             motion_windows=[(600, 700)], seed=3)
    config = make_config(links=[
        {"mac": "34:31:C4:00:00:20", "zone": "flur"},
        {"mac": "AA:BB:CC:00:00:03", "zone": "wohnzimmer"},
        {"mac": "AA:BB:CC:00:00:04", "zone": "wohnzimmer"},
    ])
    storage = Storage(tmp_path)
    engine = Engine(config, source, storage=storage,
                    clock=lambda: source.start_ts + source.elapsed)

    zonen_gesehen = set()
    for _ in range(400):
        engine.tick()
        kurz = engine.snapshot(brief=True)
        assert "zones" in kurz, "zones fehlt im Zustand"
        assert "triggered" in kurz
        zonen_gesehen.update(kurz["zones"])

    assert zonen_gesehen, "Bei erkannter Bewegung wurde keine Zone gemeldet"
    assert zonen_gesehen <= {"flur", "wohnzimmer"}
    storage.close()
