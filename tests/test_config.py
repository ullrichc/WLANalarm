import pytest

from wlanalarm.config import ConfigError, config_from_dict, load_config

from conftest import make_config


def test_pflichtfelder_werden_verlangt():
    with pytest.raises(ConfigError, match="username"):
        config_from_dict({})
    with pytest.raises(ConfigError, match="password"):
        config_from_dict({"fritzbox": {"username": "u"}})


def test_tippfehler_werden_gemeldet_statt_stillschweigend_ignoriert():
    with pytest.raises(ConfigError, match="intervall"):
        make_config(sampling={"intervall": 2})


def test_verschachtelte_abschnitte_werden_aufgebaut():
    config = make_config(alarm={"schedule": {"enabled": True, "arm_at": "22:30"}})
    assert config.alarm.schedule.arm_at == "22:30"
    assert config.alarm.schedule.arm_mode == "armed_night"


def test_umgebungsvariablen_werden_ersetzt(monkeypatch):
    monkeypatch.setenv("TEST_PW", "s3cret")
    config = config_from_dict(
        {"fritzbox": {"username": "u", "password": "${env:TEST_PW}"}}
    )
    assert config.fritzbox.password == "s3cret"


def test_umgebungsvariable_mit_vorgabewert(monkeypatch):
    monkeypatch.delenv("FEHLT_SICHER", raising=False)
    config = config_from_dict(
        {"fritzbox": {"username": "u", "password": "${env:FEHLT_SICHER:vorgabe}"}}
    )
    assert config.fritzbox.password == "vorgabe"


def test_fehlende_umgebungsvariable_ohne_vorgabe_ist_ein_fehler(monkeypatch):
    monkeypatch.delenv("FEHLT_SICHER", raising=False)
    with pytest.raises(ConfigError, match="FEHLT_SICHER"):
        config_from_dict({"fritzbox": {"username": "u", "password": "${env:FEHLT_SICHER}"}})


def test_offener_webserver_ohne_token_wird_abgelehnt():
    """Sonst koennte jedes Geraet im Heimnetz die Anlage entschaerfen."""
    with pytest.raises(ConfigError, match="token"):
        make_config(web={"host": "0.0.0.0"})
    make_config(web={"host": "0.0.0.0", "token": "geheim"})  # mit Token in Ordnung


def test_taktzeiten_muessen_zueinander_passen():
    with pytest.raises(ConfigError, match="window_seconds"):
        make_config(sampling={"interval": 5.0, "window_seconds": 6.0})
    with pytest.raises(ConfigError, match="baseline_seconds"):
        make_config(sampling={"window_seconds": 60.0, "baseline_seconds": 30.0})


def test_zu_schnelles_polling_wird_abgelehnt():
    with pytest.raises(ConfigError, match="0.5"):
        make_config(sampling={"interval": 0.2})


def test_clear_score_muss_unter_trigger_score_liegen():
    with pytest.raises(ConfigError, match="clear_score"):
        make_config(detector={"trigger_score": 0.4, "clear_score": 0.6})


def test_doppelte_mac_in_links_wird_gemeldet():
    with pytest.raises(ConfigError, match="mehrfach"):
        make_config(links=[{"mac": "AA:BB:CC:DD:EE:01"}, {"mac": "aa-bb-cc-dd-ee-01"}])


def test_link_lookup_normalisiert_die_mac():
    config = make_config(links=[{"mac": "aa-bb-cc-dd-ee-01", "zone": "flur"}])
    assert config.link_config("AA:BB:CC:DD:EE:01").zone == "flur"
    assert config.link_config("00:00:00:00:00:00") is None


def test_unbekannter_benachrichtigungslevel_wird_gemeldet():
    with pytest.raises(ConfigError, match="min_level"):
        make_config(notifiers=[{"type": "log", "min_level": "sehr wichtig"}])


def test_zeitplan_prueft_das_uhrzeitformat():
    with pytest.raises(ConfigError, match="HH:MM"):
        make_config(alarm={"schedule": {"enabled": True, "arm_at": "23 Uhr"}})


def test_anwesenheit_ohne_geraete_ist_sinnlos():
    with pytest.raises(ConfigError, match="macs"):
        make_config(alarm={"presence": {"enabled": True}})


def test_schlanke_vorlage_ist_gueltig(monkeypatch):
    monkeypatch.setenv("FRITZ_PASSWORD", "x")
    config = load_config("src/wlanalarm/config.example.yaml")
    assert config.fritzbox.username == "wlanalarm"
    # Die Vorlage schreibt keine Vorgaben fest - sonst kämen Verbesserungen
    # bei bestehenden Installationen nie an.
    assert config.selection.bands == []
    assert config.links == []


def test_vollstaendige_vorlage_ist_gueltig(monkeypatch):
    monkeypatch.setenv("FRITZ_PASSWORD", "x")
    config = load_config("src/wlanalarm/config.full.example.yaml")
    assert config.fritzbox.username == "wlanalarm"
    assert any(link.ignore for link in config.links)


def test_fehlende_datei_wird_gemeldet():
    with pytest.raises(ConfigError, match="nicht gefunden"):
        load_config("/gibt/es/nicht.yaml")


def test_alle_einstellungen_sind_dokumentiert():
    """Eine Tabelle, die Parameter auslässt, ist schlimmer als keine: Wer nach
    ihr seine Werte nachrechnet, bekommt andere Zahlen als das Programm."""
    import dataclasses
    from pathlib import Path

    from wlanalarm.config import DetectorConfig, SamplingConfig

    doku = Path("docs/kalibrierung-und-tuning.md").read_text(encoding="utf-8")
    fehlt = [
        f"{praefix}.{feld.name}"
        for cls, praefix in ((DetectorConfig, "detector"), (SamplingConfig, "sampling"))
        for feld in dataclasses.fields(cls)
        if f"{praefix}.{feld.name}" not in doku
    ]
    assert fehlt == [], f"Nicht dokumentierte Einstellungen: {fehlt}"


def test_vollstaendige_vorlage_nennt_alle_detector_werte():
    import dataclasses
    from pathlib import Path

    from wlanalarm.config import DetectorConfig

    vorlage = Path("src/wlanalarm/config.full.example.yaml").read_text(encoding="utf-8")
    fehlt = [f.name for f in dataclasses.fields(DetectorConfig) if f.name not in vorlage]
    assert fehlt == [], f"Nicht in der Vorlage: {fehlt}"
