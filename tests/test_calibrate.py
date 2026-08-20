from wlanalarm.calibrate import calibrate
from wlanalarm.sources.synthetic import SyntheticLink, SyntheticSource

from conftest import make_config


class Uhr:
    """Simulierte Zeit, damit die Kalibrierung nicht wirklich zehn Minuten dauert."""

    def __init__(self, start: float = 1_700_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def kalibriere(links, config=None, duration=600.0):
    config = config or make_config()
    uhr = Uhr()
    source = SyntheticSource(links, interval=config.sampling.interval, seed=5)
    return calibrate(config, source, duration=duration, clock=uhr, sleep=uhr.sleep), config


def test_ruhige_strecken_bekommen_gute_noten():
    links = [
        SyntheticLink("Repeater", "34:31:C4:00:00:20", base_rssi=-47,
                      quiet_noise_db=0.3, peer_is_mesh_node=True),
        SyntheticLink("Fernseher", "AA:BB:CC:00:00:03", base_rssi=-58, quiet_noise_db=0.6),
    ]
    result, _ = kalibriere(links)
    assert result.scans == 300
    assert len(result.usable) == 2
    assert result.links[0].grade == "sehr gut"
    assert result.links[0].is_mesh_node


def test_unruhige_strecke_wird_aussortiert():
    links = [
        SyntheticLink("Repeater", "34:31:C4:00:00:20", base_rssi=-47,
                      quiet_noise_db=0.3, peer_is_mesh_node=True),
        SyntheticLink("Wackelkamera", "AA:BB:CC:00:00:07", base_rssi=-74, quiet_noise_db=3.0),
    ]
    result, _ = kalibriere(links)
    wackel = next(r for r in result.links if r.peer_name == "Wackelkamera")
    assert not wackel.usable
    assert wackel.grade == "ungeeignet"


def test_baselines_stammen_nur_aus_brauchbaren_strecken():
    links = [
        SyntheticLink("Repeater", "34:31:C4:00:00:20", base_rssi=-47,
                      quiet_noise_db=0.3, peer_is_mesh_node=True),
        SyntheticLink("Wackelkamera", "AA:BB:CC:00:00:07", base_rssi=-74, quiet_noise_db=3.0),
    ]
    result, _ = kalibriere(links)
    baselines = result.baselines()
    assert len(baselines) == 1
    schnappschuss = next(iter(baselines.values()))
    assert schnappschuss.median > 0
    assert schnappschuss.samples > 100


def test_hinweis_bei_zu_wenigen_strecken():
    links = [SyntheticLink("Fernseher", "AA:BB:CC:00:00:03", base_rssi=-58,
                           quiet_noise_db=0.5)]
    result, config = kalibriere(links)
    hinweise = " ".join(result.advice(config))
    assert "min_links" in hinweise


def test_hinweis_wenn_kein_repeater_dabei_ist():
    links = [
        SyntheticLink("Fernseher", "AA:BB:CC:00:00:03", base_rssi=-58, quiet_noise_db=0.5),
        SyntheticLink("Lautsprecher", "AA:BB:CC:00:00:04", base_rssi=-55, quiet_noise_db=0.5),
    ]
    result, config = kalibriere(links)
    assert any("Repeater" in note for note in result.advice(config))


def test_hinweis_bei_schwachem_signal():
    links = [
        SyntheticLink("Repeater", "34:31:C4:00:00:20", base_rssi=-47,
                      quiet_noise_db=0.3, peer_is_mesh_node=True),
        SyntheticLink("Ferne Kamera", "AA:BB:CC:00:00:07", base_rssi=-78, quiet_noise_db=0.6),
    ]
    result, config = kalibriere(links)
    assert any("Schwaches Signal" in note for note in result.advice(config))


def test_hinweis_wenn_nur_24_ghz_verwendet_wird():
    links = [
        SyntheticLink("Steckdose", "AA:BB:CC:00:00:05", band="2.4 GHz",
                      base_rssi=-60, quiet_noise_db=0.5),
        SyntheticLink("Drucker", "AA:BB:CC:00:00:08", band="2.4 GHz",
                      base_rssi=-62, quiet_noise_db=0.5),
    ]
    config = make_config(
        selection={"bands": ["2.4 GHz"]},
        links=[{"mac": "AA:BB:CC:00:00:05"}, {"mac": "AA:BB:CC:00:00:08"}],
    )
    result, _ = kalibriere(links, config)
    assert any("2,4 GHz" in note for note in result.advice(config))


def test_ohne_brauchbare_strecke_gibt_es_eine_klare_ansage():
    result, config = kalibriere([], duration=60.0)
    assert result.links == []
    assert "Keine einzige brauchbare" in result.advice(config)[0]


# --- Strecken, die keine Information liefern ------------------------------ #


def test_eingefrorener_messwert_gilt_als_ungeeignet():
    """Ein Wert, der sich nie ändert, sieht wie ein vollkommen ruhiger Sensor
    aus - trägt aber keinerlei Information. Was sich in Ruhe nicht regt, regt
    sich auch bei Bewegung nicht."""
    from wlanalarm.calibrate import _beurteilen

    grade, note, usable = _beurteilen(
        activity_median=0.0, coverage=1.0, messpunkte=400,
        change_rate=0.0, distinct_values=1,
    )
    assert grade == "ungeeignet"
    assert not usable
    assert "kein einziges Mal geaendert" in note


def test_zu_traege_aktualisierung_gilt_als_ungeeignet():
    from wlanalarm.calibrate import _beurteilen

    grade, _, usable = _beurteilen(0.2, 1.0, 400, change_rate=0.04, distinct_values=8)
    assert grade == "ungeeignet"
    assert not usable


def test_note_und_begruendung_widersprechen_sich_nicht():
    """Regression: Eine Strecke, die nur in 13 % der Messungen sichtbar war,
    stand mit der Note 'sehr gut' da - der Ausschlussgrund war zur
    Randbemerkung verkommen."""
    from wlanalarm.calibrate import _beurteilen

    grade, note, usable = _beurteilen(0.0, coverage=0.13, messpunkte=400,
                                      change_rate=0.5, distinct_values=30)
    assert grade == "ungeeignet"
    assert not usable
    assert "13%" in note


def test_lebendige_ruhige_strecke_bleibt_sehr_gut():
    from wlanalarm.calibrate import _beurteilen

    grade, _, usable = _beurteilen(0.25, 1.0, 400, change_rate=0.8, distinct_values=40)
    assert (grade, usable) == ("sehr gut", True)


def test_hinweis_bei_zu_traeger_aktualisierung():
    """Der Fall, der jede Feinabstimmung vergeblich macht - er muss benannt
    werden, samt dem, was tatsächlich hilft."""
    links = [
        SyntheticLink("Repeater", "34:31:C4:00:00:20", base_rssi=-47,
                      quiet_noise_db=0.3, peer_is_mesh_node=True),
        SyntheticLink("Steckdose", "AA:BB:CC:00:00:05", base_rssi=-40,
                      quiet_noise_db=0.0),   # liefert immer denselben Wert
    ]
    result, config = kalibriere(links)
    steckdose = next(r for r in result.links if r.peer_name == "Steckdose")
    assert not steckdose.usable
    hinweise = " ".join(result.advice(config))
    assert "erneuert die FRITZ!Box den Messwert zu selten" in hinweise
    assert "Fernseher" in hinweise
