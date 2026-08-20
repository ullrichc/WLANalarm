from datetime import datetime

import pytest

from wlanalarm.alarm import (
    STATE_ALARM,
    STATE_ARMING,
    STATE_DISARMED,
    STATE_PENDING,
    AlarmPanel,
    ScheduleController,
)
from wlanalarm.config import AlarmConfig, ScheduleConfig
from wlanalarm.detector import DetectionResult, LinkResult


def bewegung(ts: float, zonen: list[str] | None = None) -> DetectionResult:
    zonen = zonen or ["flur"]
    links = [
        LinkResult(
            link_id="l1", label="Box <-> TV", peer_mac="AA", peer_name="Fernseher",
            zone=zonen[0], band="5 GHz", weight=1.0, activity_db=5.0, baseline_db=1.0,
            baseline_scale_db=0.4, z=10.0, score=1.0, triggered=True, ready=True,
            window_samples=6, rssi_dbm=-55.0,
        )
    ]
    return DetectionResult(ts=ts, motion=True, score=1.0, ready=True,
                           links=links, triggered_links=links, zones=zonen)


def ruhe(ts: float) -> DetectionResult:
    return DetectionResult(ts=ts, motion=False, score=0.0, ready=True)


def typen(events) -> list[str]:
    return [event.type for event in events]


def test_startzustand_ist_unscharf():
    panel = AlarmPanel(AlarmConfig(), now=0.0)
    assert panel.state == STATE_DISARMED
    assert not panel.is_armed


def test_startmodus_aus_der_konfiguration_gilt_sofort():
    panel = AlarmPanel(AlarmConfig(initial_mode="armed_away"), now=0.0)
    assert panel.state == "armed_away"


def test_ausgangsverzoegerung_laeuft_ab():
    panel = AlarmPanel(AlarmConfig(exit_delay=30.0), now=0.0)
    assert typen(panel.arm("armed_away", 0.0)) == ["arming"]
    assert panel.state == STATE_ARMING

    assert typen(panel.update(10.0, ruhe(10.0))) == []
    assert typen(panel.update(31.0, ruhe(31.0))) == ["armed"]
    assert panel.state == "armed_away"


def test_bewegung_waehrend_der_ausgangsverzoegerung_loest_nicht_aus():
    """Sonst schlaegt die Anlage an, waehrend man selbst zur Tuer geht."""
    panel = AlarmPanel(AlarmConfig(exit_delay=60.0), now=0.0)
    panel.arm("armed_away", 0.0)
    events = panel.update(20.0, bewegung(20.0))
    assert "pending" not in typen(events)
    assert panel.state == STATE_ARMING


def test_eingangsverzoegerung_und_alarm():
    panel = AlarmPanel(AlarmConfig(exit_delay=0.0, entry_delay=30.0), now=0.0)
    panel.arm("armed_away", 0.0, instant=True)

    events = panel.update(100.0, bewegung(100.0))
    assert "pending" in typen(events)
    assert panel.state == STATE_PENDING

    assert typen(panel.update(115.0, ruhe(115.0))) == ["motion_cleared"]
    assert panel.state == STATE_PENDING

    events = panel.update(131.0, ruhe(131.0))
    assert "alarm" in typen(events)
    assert panel.state == STATE_ALARM


def test_ohne_eingangsverzoegerung_gibt_es_sofort_alarm():
    panel = AlarmPanel(AlarmConfig(exit_delay=0.0, entry_delay=0.0), now=0.0)
    panel.arm("armed_away", 0.0, instant=True)
    assert "alarm" in typen(panel.update(50.0, bewegung(50.0)))


def test_entschaerfen_beendet_den_alarm():
    panel = AlarmPanel(AlarmConfig(exit_delay=0.0, entry_delay=0.0), now=0.0)
    panel.arm("armed_away", 0.0, instant=True)
    panel.update(50.0, bewegung(50.0))
    assert typen(panel.disarm(60.0)) == ["disarmed"]
    assert panel.state == STATE_DISARMED


def test_sperrzeit_nach_einem_alarm():
    config = AlarmConfig(exit_delay=0.0, entry_delay=0.0, cooldown=300.0)
    panel = AlarmPanel(config, now=0.0)
    panel.arm("armed_away", 0.0, instant=True)
    panel.update(50.0, bewegung(50.0))
    panel.disarm(60.0)
    panel.arm("armed_away", 61.0, instant=True)

    # Innerhalb der Sperrzeit passiert nichts.
    assert "alarm" not in typen(panel.update(100.0, bewegung(100.0)))
    # Danach wieder.
    assert "alarm" in typen(panel.update(400.0, bewegung(400.0)))


def test_zonen_begrenzen_die_ueberwachung():
    config = AlarmConfig(exit_delay=0.0, entry_delay=0.0, zones_armed_home=["flur"])
    panel = AlarmPanel(config, now=0.0)
    panel.arm("armed_home", 0.0, instant=True)

    assert "alarm" not in typen(panel.update(10.0, bewegung(10.0, ["wohnzimmer"])))
    assert "alarm" in typen(panel.update(20.0, bewegung(20.0, ["flur"])))


def test_leere_zonenliste_bedeutet_alle_zonen():
    panel = AlarmPanel(AlarmConfig(exit_delay=0.0, entry_delay=0.0), now=0.0)
    panel.arm("armed_away", 0.0, instant=True)
    assert "alarm" in typen(panel.update(10.0, bewegung(10.0, ["irgendwo"])))


def test_bewegungsereignisse_kommen_auch_im_unscharfen_zustand():
    panel = AlarmPanel(AlarmConfig(), now=0.0)
    events = panel.update(10.0, bewegung(10.0))
    assert typen(events) == ["motion"]
    assert events[0].level == "motion"
    assert typen(panel.update(20.0, ruhe(20.0))) == ["motion_cleared"]


def test_bewegung_im_scharfen_zustand_hat_hoeheren_rang():
    panel = AlarmPanel(AlarmConfig(exit_delay=0.0, entry_delay=60.0), now=0.0)
    panel.arm("armed_away", 0.0, instant=True)
    events = panel.update(10.0, bewegung(10.0))
    assert next(e for e in events if e.type == "motion").level == "armed_motion"


def test_unbekannter_modus_wird_abgelehnt():
    panel = AlarmPanel(AlarmConfig(), now=0.0)
    with pytest.raises(ValueError):
        panel.arm("armed_spaceship", 0.0)


def test_status_meldet_die_restzeit():
    panel = AlarmPanel(AlarmConfig(exit_delay=60.0), now=0.0)
    panel.arm("armed_away", 0.0)
    assert panel.status(10.0)["remaining"] == 50.0


# -- Zeitplan ------------------------------------------------------------- #


def stamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def test_zeitplan_schaltet_beim_ueberschreiten_der_uhrzeit():
    schedule = ScheduleController(
        ScheduleConfig(enabled=True, arm_at="23:00", disarm_at="06:30")
    )
    assert schedule.due(stamp("2026-03-10 22:59:00")) is None
    assert schedule.due(stamp("2026-03-10 22:59:30")) is None

    action, reason = schedule.due(stamp("2026-03-10 23:00:30"))
    assert action == "armed_night"
    assert "23:00" in reason

    assert schedule.due(stamp("2026-03-10 23:05:00")) is None
    assert schedule.due(stamp("2026-03-11 06:30:10"))[0] == "disarmed"


def test_zeitplan_beachtet_wochentage():
    # 0 = Montag; der 10.3.2026 ist ein Dienstag.
    schedule = ScheduleController(
        ScheduleConfig(enabled=True, arm_at="23:00", disarm_at="06:30", weekdays=[5, 6])
    )
    schedule.due(stamp("2026-03-10 22:59:00"))
    assert schedule.due(stamp("2026-03-10 23:00:30")) is None


def test_abgeschalteter_zeitplan_macht_nichts():
    schedule = ScheduleController(ScheduleConfig(enabled=False, arm_at="23:00"))
    schedule.due(stamp("2026-03-10 22:59:00"))
    assert schedule.due(stamp("2026-03-10 23:01:00")) is None
