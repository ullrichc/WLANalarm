import time

from wlanalarm.alarm import AlarmEvent
from wlanalarm.baseline import BaselineSnapshot
from wlanalarm.storage import Storage


def ereignis(level="alarm", ts=None, message="Test"):
    return AlarmEvent(ts=ts or time.time(), type=level, level=level,
                      message=message, mode="armed_away", zones=["flur"], links=["TV"])


def test_ereignisse_werden_gespeichert_und_gelesen(tmp_path):
    storage = Storage(tmp_path)
    storage.add_event(ereignis(message="Erstes"))
    storage.add_event(ereignis(message="Zweites"))
    events = storage.recent_events()
    assert [e["message"] for e in events] == ["Zweites", "Erstes"]
    assert events[0]["zones"] == ["flur"]
    assert events[0]["links"] == ["TV"]
    storage.close()


def test_filter_nach_rang(tmp_path):
    storage = Storage(tmp_path)
    storage.add_event(ereignis(level="motion", message="Bewegung"))
    storage.add_event(ereignis(level="alarm", message="Alarm"))
    assert [e["message"] for e in storage.recent_events(min_level="alarm")] == ["Alarm"]
    assert len(storage.recent_events(min_level="motion")) == 2
    storage.close()


def test_alte_ereignisse_werden_geloescht(tmp_path):
    storage = Storage(tmp_path)
    storage.add_event(ereignis(ts=time.time() - 100 * 86400, message="Alt"))
    storage.add_event(ereignis(message="Neu"))
    assert storage.purge_events(retention_days=90) == 1
    assert [e["message"] for e in storage.recent_events()] == ["Neu"]
    storage.close()


def test_baselines_ueberleben_einen_neustart(tmp_path):
    storage = Storage(tmp_path)
    storage.save_baselines({"l1": BaselineSnapshot(median=1.5, scale=0.4, samples=100)})
    storage.close()

    storage = Storage(tmp_path)
    geladen = storage.load_baselines()
    assert geladen["l1"].median == 1.5
    assert geladen["l1"].samples == 100
    storage.close()


def test_baselines_werden_aktualisiert_statt_verdoppelt(tmp_path):
    storage = Storage(tmp_path)
    storage.save_baselines({"l1": BaselineSnapshot(median=1.0, scale=0.1, samples=10)})
    storage.save_baselines({"l1": BaselineSnapshot(median=2.0, scale=0.2, samples=20)})
    geladen = storage.load_baselines()
    assert len(geladen) == 1
    assert geladen["l1"].median == 2.0
    storage.close()


def test_veraltete_baselines_werden_verworfen(tmp_path):
    """Eine ein halbes Jahr alte Baseline beschreibt die Wohnung von damals."""
    storage = Storage(tmp_path)
    alt = BaselineSnapshot(median=1.0, scale=0.1, samples=10,
                           updated=time.time() - 200 * 86400)
    storage.save_baselines({"l1": alt})
    assert storage.load_baselines(max_age_days=30) == {}
    assert "l1" in storage.load_baselines(max_age_days=365)
    storage.close()


def test_zustand_wird_gehalten(tmp_path):
    storage = Storage(tmp_path)
    assert storage.get_state("mode", "disarmed") == "disarmed"
    storage.set_state("mode", "armed_night")
    storage.set_state("mode", "armed_away")
    assert storage.get_state("mode") == "armed_away"
    storage.close()
