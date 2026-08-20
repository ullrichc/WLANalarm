"""Tests der Kommandozeile mit einer vorgetaeuschten FRITZ!Box."""

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from wlanalarm import cli
from wlanalarm.model import Scan
from wlanalarm.sources.base import SampleSource, SourceError
from wlanalarm.sources.mesh_parser import parse_mesh_list

DATA = Path(__file__).parent / "data"


class FakeSource(SampleSource):
    """Spielt die Mesh-Fixture ab, mit leichtem Rauschen auf dem RSSI -
    ein exakt konstanter Messwert kommt an echter Hardware nicht vor."""

    name = "fake"

    def __init__(self, mesh_json, fehler=False):
        self._mesh = mesh_json
        self._fehler = fehler
        self._ts = 1_700_000_000.0
        self._random = random.Random(42)
        self.geschlossen = False

    def scan(self) -> Scan:
        if self._fehler:
            raise SourceError("FRITZ!Box nicht erreichbar")
        self._ts += 2.0
        scan = parse_mesh_list(self._mesh, self._ts)
        scan.samples = [
            replace(sample, rssi_dbm=round(sample.rssi_dbm + self._random.gauss(0, 0.5), 1))
            if sample.rssi_dbm is not None else sample
            for sample in scan.samples
        ]
        return scan

    def check(self) -> str:
        return "FRITZ!Box 5690 Pro, FRITZ!OS 8.20"

    def close(self) -> None:
        self.geschlossen = True


@pytest.fixture
def config_datei(tmp_path):
    pfad = tmp_path / "config.yaml"
    pfad.write_text(
        yaml.safe_dump({
            "fritzbox": {"username": "test", "password": "test"},
            "storage": {"directory": str(tmp_path / "state")},
            "web": {"enabled": False},
        }),
        encoding="utf-8",
    )
    return pfad


@pytest.fixture
def fake_box(monkeypatch, mesh_json):
    source = FakeSource(mesh_json)
    monkeypatch.setattr(cli, "build_source", lambda config: source)
    return source


def test_check_meldet_erfolg(config_datei, fake_box, capsys):
    assert cli.main(["check", "-c", str(config_datei)]) == 0
    ausgabe = capsys.readouterr().out
    assert "in Ordnung" in ausgabe
    assert "FRITZ!Box 5690 Pro" in ausgabe
    assert "5 nutzbar" in ausgabe


def test_check_warnt_wenn_zu_wenige_strecken_da_sind(tmp_path, fake_box, capsys):
    pfad = tmp_path / "config.yaml"
    pfad.write_text(
        yaml.safe_dump({
            "fritzbox": {"username": "t", "password": "t"},
            "storage": {"directory": str(tmp_path / "state")},
            "selection": {"max_links": 1},
            "detector": {"min_links": 3},
        }),
        encoding="utf-8",
    )
    assert cli.main(["check", "-c", str(pfad)]) == 1
    assert "ACHTUNG" in capsys.readouterr().out


def test_check_meldet_verbindungsfehler(config_datei, monkeypatch, mesh_json, capsys):
    monkeypatch.setattr(cli, "build_source", lambda config: FakeSource(mesh_json, fehler=True))
    assert cli.main(["check", "-c", str(config_datei)]) == 2
    assert "nicht erreichbar" in capsys.readouterr().err


def test_discover_listet_die_strecken(config_datei, fake_box, capsys):
    assert cli.main(["discover", "-c", str(config_datei)]) == 0
    ausgabe = capsys.readouterr().out
    assert "Repeater Flur" in ausgabe
    assert "Steckdose Kueche" in ausgabe
    assert "5 Strecke(n) werden als Sensor verwendet." in ausgabe


def test_discover_als_json(config_datei, fake_box, capsys):
    assert cli.main(["discover", "-c", str(config_datei), "--json"]) == 0
    eintraege = json.loads(capsys.readouterr().out)
    assert len(eintraege) == 5
    assert {e["peer_name"] for e in eintraege if e["selected"]} == {
        "Repeater Flur", "Fernseher", "Sonos Wohnzimmer",
        "Kamera Eingang", "Steckdose Kueche",
    }


def test_calibrate_speichert_baselines(config_datei, fake_box, monkeypatch, capsys, tmp_path):
    # Die Zeit wird simuliert, damit der Test nicht wirklich Minuten braucht.
    uhr = {"t": 1_700_000_000.0}
    monkeypatch.setattr(cli.time, "time", lambda: uhr["t"])
    import wlanalarm.calibrate as kalibrierung

    monkeypatch.setattr(kalibrierung.time, "time", lambda: uhr["t"])

    def fake_calibrate(config, source, duration, progress=None, **kwargs):
        def schlafen(sekunden):
            uhr["t"] += sekunden

        return kalibrierung.calibrate(
            config, source, duration=duration, progress=progress,
            clock=lambda: uhr["t"], sleep=schlafen,
        )

    monkeypatch.setattr(cli, "calibrate", fake_calibrate)

    assert cli.main(["calibrate", "-c", str(config_datei), "--minutes", "5"]) == 0
    ausgabe = capsys.readouterr().out
    assert "Baselines gespeichert" in ausgabe

    from wlanalarm.storage import Storage

    storage = Storage(tmp_path / "state")
    assert len(storage.load_baselines()) >= 1
    storage.close()


def test_calibrate_speichert_bei_dry_run_nichts(config_datei, fake_box, monkeypatch, capsys, tmp_path):
    uhr = {"t": 1_700_000_000.0}
    import wlanalarm.calibrate as kalibrierung

    def fake_calibrate(config, source, duration, progress=None, **kwargs):
        return kalibrierung.calibrate(
            config, source, duration=duration, progress=progress,
            clock=lambda: uhr["t"], sleep=lambda s: uhr.__setitem__("t", uhr["t"] + s),
        )

    monkeypatch.setattr(cli, "calibrate", fake_calibrate)
    assert cli.main(["calibrate", "-c", str(config_datei), "--minutes", "5", "--dry-run"]) == 0
    assert "nichts gespeichert" in capsys.readouterr().out
    assert not (tmp_path / "state" / "wlanalarm.sqlite3").exists()


def test_replay_rechnet_eine_aufzeichnung_durch(config_datei, fake_box, tmp_path, capsys):
    """`record` selbst laeuft bis Strg-C und wird deshalb hier nicht ueber die
    Kommandozeile aufgerufen - der Recorder ist in test_recorder_replay.py
    abgedeckt. Getestet wird, dass `replay` dessen Dateien versteht."""
    from wlanalarm.recorder import Recorder

    with Recorder(tmp_path / "rec") as recorder:
        for _ in range(400):
            recorder.write(fake_box.scan())
    datei = next((tmp_path / "rec").glob("samples-*.ndjson"))

    assert cli.main(["replay", str(datei), "-c", str(config_datei)]) == 0
    ausgabe = capsys.readouterr().out
    assert "400 Messzyklen" in ausgabe
    assert "Bewegungsepisoden: 0" in ausgabe


def test_init_config_legt_eine_vorlage_an(tmp_path, capsys):
    ziel = tmp_path / "neu.yaml"
    assert cli.main(["init-config", str(ziel)]) == 0
    assert "fritzbox:" in ziel.read_text(encoding="utf-8")

    assert cli.main(["init-config", str(ziel)]) == 1
    assert "existiert bereits" in capsys.readouterr().err
    assert cli.main(["init-config", str(ziel), "--force"]) == 0


def test_konfigurationsfehler_wird_verstaendlich_gemeldet(tmp_path, capsys):
    pfad = tmp_path / "kaputt.yaml"
    pfad.write_text("fritzbox:\n  username: nur_der_name\n", encoding="utf-8")
    assert cli.main(["check", "-c", str(pfad)]) == 2
    assert "password" in capsys.readouterr().err


def test_test_notify_meldet_kaputte_kanaele(tmp_path, capsys):
    pfad = tmp_path / "config.yaml"
    pfad.write_text(
        yaml.safe_dump({
            "fritzbox": {"username": "t", "password": "t"},
            "storage": {"directory": str(tmp_path / "state")},
            "notifiers": [{"type": "ntfy", "min_level": "alarm",
                           "options": {"server": "http://127.0.0.1:1", "topic": "x"}}],
        }),
        encoding="utf-8",
    )
    assert cli.main(["test-notify", "-c", str(pfad)]) == 1
    assert "FEHLER" in capsys.readouterr().out


def test_diagnose_erklaert_die_mesh_liste(config_datei, monkeypatch, mesh_json, capsys):
    """Der Bericht muss zeigen, welche Felder vorkommen und was daraus wird."""
    class MeshQuelle(FakeSource):
        def fetch_mesh_list(self):
            return self._mesh

    monkeypatch.setattr(cli, "build_source", lambda config: MeshQuelle(mesh_json))
    assert cli.main(["diagnose", "-c", str(config_datei)]) == 0
    ausgabe = capsys.readouterr().out
    assert "vorhandene Felder" in ausgabe
    assert "rssi" in ausgabe
    assert "Was WLANalarm daraus macht" in ausgabe
    assert "5 Funkstrecke(n) erkannt" in ausgabe


def test_diagnose_gibt_keine_geraetenamen_preis(config_datei, monkeypatch, mesh_json, capsys):
    """Der Bericht soll weitergegeben werden koennen - ohne zu verraten, wer
    im Haushalt welches Geraet besitzt."""
    class MeshQuelle(FakeSource):
        def fetch_mesh_list(self):
            return self._mesh

    monkeypatch.setattr(cli, "build_source", lambda config: MeshQuelle(mesh_json))
    cli.main(["diagnose", "-c", str(config_datei)])
    ausgabe = capsys.readouterr().out
    for geheim in ("Fernseher", "Sonos Wohnzimmer", "Repeater Flur",
                   "AA:BB:CC", "34:31:C4", "Zuhause"):
        assert geheim not in ausgabe, f"{geheim!r} steht im Bericht"


def test_diagnose_schreibt_in_eine_datei(config_datei, monkeypatch, mesh_json, tmp_path, capsys):
    class MeshQuelle(FakeSource):
        def fetch_mesh_list(self):
            return self._mesh

    monkeypatch.setattr(cli, "build_source", lambda config: MeshQuelle(mesh_json))
    ziel = tmp_path / "diagnose.txt"
    assert cli.main(["diagnose", "-c", str(config_datei), "-o", str(ziel)]) == 0
    assert "Schema-Version" in ziel.read_text(encoding="utf-8")


def test_discover_erzeugt_einen_fertigen_konfigurationsblock(config_datei, fake_box, capsys):
    """Die Lücke zwischen 'discover zeigt meine Geräte' und 'wie schreibe ich
    das in config.yaml' - dort werden MAC-Adressen gebraucht."""
    assert cli.main(["discover", "-c", str(config_datei), "--yaml"]) == 0
    ausgabe = capsys.readouterr().out
    assert ausgabe.startswith("# Von 'wlanalarm discover --yaml' erzeugt.")
    assert "links:" in ausgabe
    assert "AA:BB:CC:00:00:03" in ausgabe      # MAC steht drin
    assert 'name: "Fernseher"' in ausgabe
    # Alles auskommentiert - nichts wird ungefragt aktiv.
    for zeile in ausgabe.splitlines():
        if zeile.strip() and zeile.strip() != "links:":
            assert zeile.lstrip().startswith("#"), zeile


def test_discover_zeigt_die_mac_adressen(config_datei, fake_box, capsys):
    cli.main(["discover", "-c", str(config_datei)])
    assert "AA:BB:CC:00:00:03" in capsys.readouterr().out


def test_discover_weist_auf_zu_wenige_strecken_hin(tmp_path, fake_box, capsys):
    pfad = tmp_path / "config.yaml"
    pfad.write_text(
        yaml.safe_dump({
            "fritzbox": {"username": "t", "password": "t"},
            "storage": {"directory": str(tmp_path / "state")},
            "selection": {"bands": ["6 GHz"], "always_use_mesh_nodes": False},
            "detector": {"min_links": 3},
        }),
        encoding="utf-8",
    )
    cli.main(["discover", "-c", str(pfad)])
    ausgabe = capsys.readouterr().out
    assert "Das reicht nicht" in ausgabe
    assert "--yaml" in ausgabe


def test_init_config_erzeugt_eine_schlanke_vorlage(tmp_path, monkeypatch):
    """Was in der Datei steht, gilt dauerhaft - auch wenn eine neuere Version
    bessere Vorgaben mitbringt. Die Vorlage schreibt deshalb nur das Nötigste
    aus, damit Verbesserungen bei bestehenden Installationen ankommen."""
    monkeypatch.setenv("FRITZ_PASSWORD", "x")
    ziel = tmp_path / "minimal.yaml"
    assert cli.main(["init-config", str(ziel)]) == 0

    from wlanalarm.config import load_config

    config = load_config(ziel)
    # Genau die beiden Stellen, die einen realen Haushalt lahmgelegt haben:
    assert config.selection.bands == [], "Vorlage friert den Bandfilter ein"
    assert len(config.selection.ignore_name_contains) > 10, (
        "Vorlage friert die Liste mobiler Geräte ein"
    )


def test_init_config_full_enthaelt_alle_abschnitte(tmp_path, monkeypatch):
    monkeypatch.setenv("FRITZ_PASSWORD", "x")
    ziel = tmp_path / "voll.yaml"
    assert cli.main(["init-config", str(ziel), "--full"]) == 0
    inhalt = ziel.read_text(encoding="utf-8")
    for abschnitt in ("detector:", "selection:", "alarm:", "sampling:"):
        assert abschnitt in inhalt


def test_check_zeigt_die_geltenden_auswahlkriterien(config_datei, fake_box, capsys):
    """Damit sichtbar wird, wenn eine alte config.yaml den Standard überschreibt."""
    cli.main(["check", "-c", str(config_datei)])
    ausgabe = capsys.readouterr().out
    assert "Bänder      : alle" in ausgabe
    assert "min_links   : 2" in ausgabe


def test_diagnose_taent_auch_unbekannte_felder():
    """Der Bericht wird gerade dann erzeugt, wenn eine FRITZ!OS-Version
    unbekannte Felder liefert - unter einem unbekannten Schlüssel kann ebenso
    gut ein Gerätename stehen. Eine feste Schlüsselliste reicht daher nicht."""
    from wlanalarm.diagnose import _sicher

    # Frei vergebene Namen und Adressen werden ersetzt ...
    assert _sicher("voellig_neues_feld", "Fernseher Wohnzimmer").startswith("<")
    assert _sicher("irgendwas", "AA:BB:CC:00:00:03").startswith("<")
    assert _sicher("host", "aa-bb-cc-00-00-03").startswith("<")

    # ... technische Angaben und Messwerte bleiben lesbar.
    assert _sicher("opmode", "AP_MODE") == "AP_MODE"
    assert _sicher("security", "WPA3PSK") == "WPA3PSK"
    assert _sicher("channel_width", "320 MHz") == "320 MHz"
    assert _sicher("phymodes", ["ax", "be"]) == ["ax", "be"]
    assert _sicher("rx_rcpi", -58) == -58
    assert _sicher("is_meshed", True) is True
    assert _sicher("mesh_role", "master") == "master"


def test_diagnose_taent_verschachtelte_strukturen():
    from wlanalarm.diagnose import _sicher

    ergebnis = _sicher("current_channel_info", {
        "primary_freq": 5975000,
        "channel_width": "320 MHz",
        "kommentar": "Router im Wohnzimmer von Alex",
    })
    assert ergebnis["primary_freq"] == 5975000
    assert ergebnis["channel_width"] == "320 MHz"
    assert ergebnis["kommentar"].startswith("<")
