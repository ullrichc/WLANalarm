"""MQTT-Anbindung mit Home-Assistant-Discovery.

Veroeffentlicht zwei Entitaeten:

* ein ``binary_sensor`` mit device_class ``motion`` - die reine Bewegung,
* ein ``alarm_control_panel`` - Zustand der Anlage, ueber MQTT auch schaltbar.

Home Assistant legt beides nach dem Verbinden selbst an, wenn Discovery
aktiviert ist (Voreinstellung).
"""

from __future__ import annotations

import json
import logging

from ..alarm import AlarmEvent
from .base import Notifier, NotifierError

log = logging.getLogger(__name__)

#: Abbildung der internen Zustaende auf die von Home Assistant erwarteten.
_HA_STATE = {
    "disarmed": "disarmed",
    "arming": "arming",
    "armed_home": "armed_home",
    "armed_away": "armed_away",
    "armed_night": "armed_night",
    "pending": "pending",
    "alarm": "triggered",
}


class MqttNotifier(Notifier):
    type_name = "mqtt"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._client = None
        self._prefix = str(self.option("topic_prefix", "wlanalarm")).strip("/")
        self._discovery_prefix = str(self.option("discovery_prefix", "homeassistant")).strip("/")
        self._device_id = str(self.option("device_id", "wlanalarm"))
        self._discovery_sent = False
        #: Rueckruf fuer Schaltbefehle aus Home Assistant. Wird von der Engine gesetzt.
        self.command_handler = None

    # -- Verbindung -------------------------------------------------------- #

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise NotifierError(
                "Fuer den MQTT-Kanal wird paho-mqtt benoetigt. "
                "Im Projektverzeichnis nachinstallieren mit: "
                "pip install -e '.[mqtt]'"
            ) from exc

        host = self.option("host", required=True)
        port = int(self.option("port", 1883))
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.option("client_id", "wlanalarm"),
            )
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=self.option("client_id", "wlanalarm"))

        username = self.option("username")
        if username:
            client.username_pw_set(username, self.option("password"))
        if self.option("tls", False):
            client.tls_set()

        client.will_set(f"{self._prefix}/availability", "offline", retain=True)
        client.on_message = self._on_message
        client.on_connect = self._on_connect
        try:
            client.connect(host, port, keepalive=60)
        except OSError as exc:
            raise NotifierError(f"MQTT {host}:{port}: {exc}") from exc
        client.loop_start()
        self._client = client
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Nach jedem Verbindungsaufbau erneut anmelden.

        paho stellt die Verbindung nach einem Broker-Neustart selbsttaetig
        wieder her, aber Abonnements und retained Nachrichten ueberleben das
        nicht: Ohne diesen Rueckruf bliebe das "offline" aus dem Last Will
        stehen, und Schaltbefehle aus Home Assistant kaemen nicht mehr an.
        Deshalb gehoert alles Anmeldende hierher und nicht hinter connect().
        """
        if getattr(reason_code, "is_failure", False) or reason_code not in (0, None):
            log.warning("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        client.publish(f"{self._prefix}/availability", "online", retain=True)
        client.subscribe(f"{self._prefix}/command")
        # Discovery erneut senden: Ein neu aufgesetzter Broker hat die
        # retained Konfiguration sonst nicht mehr.
        self._discovery_sent = False
        self._send_discovery()
        log.info("MQTT verbunden, Themen unter %s/ angemeldet", self._prefix)

    def _send_discovery(self) -> None:
        if self._discovery_sent or not self.option("discovery", True):
            return
        if self._client is None:
            return
        device = {
            "identifiers": [self._device_id],
            "name": str(self.option("device_name", "WLANalarm")),
            "manufacturer": "WLANalarm",
            "model": "WLAN-Bewegungserkennung (FRITZ!Box)",
        }
        availability = [{"topic": f"{self._prefix}/availability"}]

        motion = {
            "name": "Bewegung",
            "unique_id": f"{self._device_id}_motion",
            "state_topic": f"{self._prefix}/state",
            "value_template": "{{ 'ON' if value_json.motion else 'OFF' }}",
            "json_attributes_topic": f"{self._prefix}/state",
            "device_class": "motion",
            "availability": availability,
            "device": device,
        }
        panel = {
            "name": "Alarmanlage",
            "unique_id": f"{self._device_id}_panel",
            "state_topic": f"{self._prefix}/state",
            "value_template": "{{ value_json.ha_state }}",
            "command_topic": f"{self._prefix}/command",
            "supported_features": ["arm_home", "arm_away", "arm_night"],
            "code_arm_required": False,
            "code_disarm_required": False,
            "availability": availability,
            "device": device,
        }
        self._client.publish(
            f"{self._discovery_prefix}/binary_sensor/{self._device_id}/motion/config",
            json.dumps(motion),
            retain=True,
        )
        self._client.publish(
            f"{self._discovery_prefix}/alarm_control_panel/{self._device_id}/panel/config",
            json.dumps(panel),
            retain=True,
        )
        self._discovery_sent = True

    def _on_message(self, client, userdata, message) -> None:  # pragma: no cover - Netzwerk
        if self.command_handler is None:
            return
        command = message.payload.decode("utf-8", errors="replace").strip().upper()
        mapping = {
            "DISARM": "disarmed",
            "ARM_HOME": "armed_home",
            "ARM_AWAY": "armed_away",
            "ARM_NIGHT": "armed_night",
        }
        target = mapping.get(command)
        if target is None:
            log.warning("Unbekannter MQTT-Befehl: %s", command)
            return
        try:
            self.command_handler(target, "mqtt")
        except Exception:
            log.exception("MQTT-Befehl %s konnte nicht ausgefuehrt werden", command)

    # -- Notifier-Schnittstelle -------------------------------------------- #

    def send(self, event: AlarmEvent) -> None:
        client = self._connect()
        client.publish(f"{self._prefix}/event", json.dumps(event.to_dict()))

    def publish_state(self, state: dict) -> None:
        client = self._connect()
        payload = dict(state)
        payload["ha_state"] = _HA_STATE.get(state.get("state", "disarmed"), "disarmed")
        client.publish(f"{self._prefix}/state", json.dumps(payload), retain=True)

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.publish(f"{self._prefix}/availability", "offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # pragma: no cover
            pass
        finally:
            self._client = None
