import time

import pytest

from wlanalarm.alarm import AlarmEvent
from wlanalarm.config import Config, NotifierConfig
from wlanalarm.notify import build_hub, build_notifier
from wlanalarm.notify.base import NotificationHub, Notifier, NotifierError


class SammelNotifier(Notifier):
    type_name = "sammel"

    def __init__(self, config):
        super().__init__(config)
        self.gesendet = []
        self.zustaende = []

    def send(self, event):
        self.gesendet.append(event)

    def publish_state(self, state):
        self.zustaende.append(state)


class KaputterNotifier(Notifier):
    type_name = "kaputt"

    def send(self, event):
        raise NotifierError("geht nicht")


def ereignis(level="alarm", type_=None):
    return AlarmEvent(ts=time.time(), type=type_ or level, level=level, message="Test")


def warte_auf(bedingung, timeout=2.0):
    ende = time.time() + timeout
    while time.time() < ende:
        if bedingung():
            return True
        time.sleep(0.01)
    return False


def test_rang_filter_haelt_bewegungsmeldungen_zurueck():
    notifier = SammelNotifier(NotifierConfig(type="sammel", min_level="alarm"))
    hub = NotificationHub([notifier])
    try:
        hub.dispatch(ereignis(level="motion"))
        hub.dispatch(ereignis(level="alarm"))
        assert warte_auf(lambda: len(notifier.gesendet) == 1)
        assert notifier.gesendet[0].level == "alarm"
    finally:
        hub.close()


def test_bewegungsmeldungen_werden_gedrosselt():
    notifier = SammelNotifier(NotifierConfig(type="sammel", min_level="motion"))
    hub = NotificationHub([notifier], rate_limit_seconds=60.0)
    try:
        for _ in range(5):
            hub.dispatch(ereignis(level="motion", type_="motion"))
        assert warte_auf(lambda: len(notifier.gesendet) >= 1)
        time.sleep(0.2)
        assert len(notifier.gesendet) == 1
    finally:
        hub.close()


def test_alarme_werden_nie_gedrosselt():
    notifier = SammelNotifier(NotifierConfig(type="sammel", min_level="alarm"))
    hub = NotificationHub([notifier], rate_limit_seconds=3600.0)
    try:
        for _ in range(3):
            hub.dispatch(ereignis(level="alarm"))
        assert warte_auf(lambda: len(notifier.gesendet) == 3)
    finally:
        hub.close()


def test_ein_kaputter_kanal_blockiert_die_anderen_nicht():
    gut = SammelNotifier(NotifierConfig(type="sammel", min_level="alarm"))
    hub = NotificationHub([KaputterNotifier(NotifierConfig(type="kaputt")), gut])
    try:
        hub.dispatch(ereignis())
        assert warte_auf(lambda: len(gut.gesendet) == 1)
    finally:
        hub.close()


def test_abgeschalteter_kanal_bekommt_nichts():
    notifier = SammelNotifier(
        NotifierConfig(type="sammel", min_level="motion", enabled=False)
    )
    hub = NotificationHub([notifier])
    try:
        hub.dispatch(ereignis(level="motion"))
        time.sleep(0.2)
        assert notifier.gesendet == []
    finally:
        hub.close()


def test_zustandsmeldungen_erreichen_die_kanaele():
    notifier = SammelNotifier(NotifierConfig(type="sammel"))
    hub = NotificationHub([notifier])
    try:
        hub.publish_state({"state": "armed_away"})
        assert warte_auf(lambda: len(notifier.zustaende) == 1)
    finally:
        hub.close()


def test_ohne_konfiguration_wird_ins_log_geschrieben():
    hub = build_hub(Config())
    try:
        assert len(hub.notifiers) == 1
        assert hub.notifiers[0].type_name == "log"
    finally:
        hub.close()


def test_unbekannter_typ_wird_gemeldet():
    with pytest.raises(NotifierError, match="Unbekannter"):
        build_notifier(NotifierConfig(type="brieftaube"))


def test_fehlende_pflichtoption_wird_gemeldet():
    notifier = build_notifier(NotifierConfig(type="ntfy", options={}))
    with pytest.raises(NotifierError, match="topic"):
        notifier.send(ereignis())


def test_kaputter_kanal_wird_beim_aufbau_uebersprungen():
    config = Config(notifiers=[
        NotifierConfig(type="brieftaube"),
        NotifierConfig(type="log", min_level="motion"),
    ])
    hub = build_hub(config)
    try:
        assert [n.type_name for n in hub.notifiers] == ["log"]
    finally:
        hub.close()
