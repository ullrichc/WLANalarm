# Benachrichtigungen und Integrationen

Jeder Kanal hat ein `min_level`, das festlegt, ab welchem Rang zugestellt wird:

| Rang | Wann |
|---|---|
| `motion` | jede erkannte Bewegung, auch bei unscharfer Anlage |
| `armed_motion` | Bewegung, während die Anlage scharf ist |
| `alarm` | die Anlage hat ausgelöst |

Bewegungsmeldungen werden auf eine pro Minute und Kanal gedrosselt. **Alarme
gehen immer sofort raus**, ohne Drosselung.

Alle Kanäle testen:

```bash
wlanalarm test-notify --level alarm
```

## Push aufs Handy (ntfy)

Der einfachste Weg. [ntfy-App](https://ntfy.sh) installieren, Thema abonnieren,
fertig – kein Konto nötig.

```yaml
notifiers:
  - type: "ntfy"
    name: "Handy"
    min_level: "alarm"
    options:
      server: "https://ntfy.sh"
      topic: "wlanalarm-a7f3k2m9x4p1q8"
      # token: "${env:NTFY_TOKEN}"     # bei eigener Instanz mit Zugangsschutz
```

> **Auf ntfy.sh kann jeder mitlesen, der den Themennamen kennt.** Deshalb einen
> langen, zufälligen Namen wählen – oder eine eigene Instanz betreiben.

Alarme kommen mit Priorität `urgent` und durchbrechen damit auch „Nicht stören".

## E-Mail

```yaml
notifiers:
  - type: "smtp"
    name: "E-Mail"
    min_level: "alarm"
    options:
      host: "smtp.example.org"
      port: 587                # 465 schaltet automatisch auf SMTPS um
      from: "alarm@example.org"
      to: ["ich@example.org", "partner@example.org"]
      username: "alarm@example.org"
      password: "${env:SMTP_PASSWORD}"
      starttls: true
```

## Webhook

```yaml
notifiers:
  - type: "webhook"
    name: "Node-RED"
    min_level: "armed_motion"
    options:
      url: "http://nodered.local:1880/wlanalarm"
      method: "POST"
      headers:
        X-Token: "${env:WEBHOOK_TOKEN}"
```

Gesendet wird das Ereignis als JSON:

```json
{
  "ts": 1755600000.0,
  "type": "alarm",
  "level": "alarm",
  "message": "ALARM - Bewegung im Modus Abwesend an: Repeater Flur, Fernseher",
  "mode": "armed_away",
  "zones": ["flur"],
  "score": 0.94,
  "links": ["Repeater Flur", "Fernseher"],
  "source": "system"
}
```

## Home Assistant über MQTT

```bash
pip install -e ".[mqtt]"
```

```yaml
notifiers:
  - type: "mqtt"
    name: "MQTT"
    min_level: "motion"
    options:
      host: "192.168.178.20"
      port: 1883
      username: "wlanalarm"
      password: "${env:MQTT_PASSWORD}"
      topic_prefix: "wlanalarm"
      discovery: true
```

Home Assistant legt daraufhin selbsttätig zwei Entitäten an:

* `binary_sensor.wlanalarm_bewegung` – device_class `motion`, mit dem
  vollständigen Zustand als Attribute
* `alarm_control_panel.wlanalarm_alarmanlage` – zeigt den Zustand **und lässt
  sich aus Home Assistant heraus schalten** (`arm_home`, `arm_away`,
  `arm_night`, `disarm`)

Verwendete Themen:

| Thema | Inhalt |
|---|---|
| `wlanalarm/state` | Zustand, retained |
| `wlanalarm/event` | einzelne Ereignisse |
| `wlanalarm/command` | Schaltbefehle nach WLANalarm hinein |
| `wlanalarm/availability` | `online` / `offline` per Last Will |

Beispielautomatisierung:

```yaml
automation:
  - alias: "Licht an bei Bewegung im Flur"
    trigger:
      - platform: state
        entity_id: binary_sensor.wlanalarm_bewegung
        to: "on"
    condition:
      - condition: template
        value_template: "{{ 'flur' in state_attr('binary_sensor.wlanalarm_bewegung', 'zones') }}"
    action:
      - service: light.turn_on
        target: { entity_id: light.flur }
```

## REST-Schnittstelle

```
GET  /                  Dashboard
GET  /api/health        {"status":"ok"} – ohne Token erreichbar
GET  /api/status        vollständiger Zustand samt Verlauf
GET  /api/events?limit=50&level=alarm
GET  /api/links         alle Funkstrecken mit Auswahlbegründung
POST /api/mode          {"mode":"armed_away"}
```

```bash
curl -s localhost:8723/api/status | jq '.state, .score'
curl -s -X POST localhost:8723/api/mode \
     -H 'Content-Type: application/json' -d '{"mode":"armed_away"}'
```

Mit Token:

```yaml
web:
  host: "0.0.0.0"
  port: 8723
  token: "${env:WEB_TOKEN}"
```

```bash
curl -s -H "Authorization: Bearer $WEB_TOKEN" localhost:8723/api/status
```

Das Dashboard nimmt das Token auch als Abfrageparameter entgegen, damit es sich
im Browser aufrufen lässt: `http://pi.local:8723/?token=...`

> Ohne Token lässt WLANalarm `host: 0.0.0.0` gar nicht erst zu – sonst könnte
> jedes Gerät im Heimnetz die Anlage entschärfen.

## Anwesenheitsautomatik

```yaml
alarm:
  presence:
    enabled: true
    macs:
      - "AA:BB:CC:DD:EE:10"     # Handy Person 1
      - "AA:BB:CC:DD:EE:11"     # Handy Person 2
    away_mode: "armed_away"
    home_mode: "disarmed"
    away_after_seconds: 300
```

> **Zwingend:** Für diese Geräte muss die MAC-Randomisierung im eigenen WLAN
> abgeschaltet sein – iOS: *WLAN* → *ⓘ* → *Private WLAN-Adresse* aus;
> Android: *Netzwerkdetails* → *Datenschutz* → *Geräte-MAC verwenden*. Sonst
> wechselt die Adresse und die Erkennung schlägt fehl.

Die fünf Minuten Karenz haben einen Grund: WLAN-Abmeldungen sind flüchtig, und
ein Handy, das kurz im Mobilfunk hängt, darf die Anlage nicht scharf schalten,
während man in der Küche steht.
