# Architektur

## Datenfluss

```
  FRITZ!Box
      │  TR-064: Hosts:1 X_AVM-DE_GetMeshListPath  →  JSON-Topologie
      ▼
  sources/         Mesh-Liste, TR-064-Ersatz, Wiedergabe, Simulation
      │            → Scan (Liste von LinkSample)
      ▼
  selection.py     welche Strecken taugen als Sensor
      │            → Menge von Link-IDs
      ▼
  detector.py      je Strecke: Aktivität → z-Wert → Score
      │            → DetectionResult
      ▼
  alarm.py         Zustandsautomat, Zonen, Verzögerungen
      │            → AlarmEvent
      ├─────────▶ storage.py    SQLite: Ereignisse, Baselines, Modus
      ├─────────▶ notify/       ntfy, SMTP, Webhook, MQTT (eigener Thread)
      ├─────────▶ recorder.py   NDJSON für spätere Wiedergabe
      └─────────▶ web/          Dashboard und REST
```

`engine.py` hält das zusammen und taktet die Schleife.

## Entwurfsentscheidungen

### Mesh-Liste statt WLANConfiguration

TR-064 kennt `WLANConfiguration:n GetGenericAssociatedDeviceInfo` – naheliegend,
aber schlecht geeignet: Die Signalstärke kommt nur in Prozent und ist grob
quantisiert. Genau die feinen Schwankungen, aus denen sich Bewegung ableiten
ließe, gehen dabei verloren. Außerdem sind Geräte hinter einem Repeater
unsichtbar.

Die Mesh-Topologie liefert echte dBm-Werte samt Datenrate, MCS und Streamzahl –
und sie enthält auch die Strecken, die an einem Repeater hängen. Ohne diese Werte ist eine
brauchbare Erkennung nicht möglich.

Der Parser (`sources/mesh_parser.py`) ist strikt von der Netzwerkschicht
getrennt und arbeitet auf einem Dict. So lässt er sich gegen aufgezeichnete
JSON-Dateien testen, ohne dass eine FRITZ!Box im Spiel ist.

### Median und MAD statt Mittelwert und Standardabweichung

Die FRITZ!Box liefert gelegentlich einen völlig danebenliegenden RSSI – etwa
wenn ein Client den Kanal wechselt. Ein einziger solcher Wert bläht eine
Standardabweichung so auf, dass danach minutenlang jede echte Bewegung unter der
Schwelle bleibt. Median und MAD interessieren sich für Einzelwerte nicht.

### Baseline je Strecke, nicht global

Jede Funkstrecke hat ihr eigenes Rauschverhalten. Eine gemeinsame absolute
Schwelle in dB müsste für die ruhigste und die unruhigste Strecke gleichzeitig
stimmen – das geht nicht. Der z-Wert normiert jede Strecke auf ihr eigenes
Ruheverhalten.

### Baseline nur in Ruhephasen fortschreiben

Wer bei Bewegung weiterlernt, macht die Bewegung nach einigen Minuten zum
Normalzustand und die Anlage verstummt. `_update_baselines` überspringt deshalb
jeden Zyklus, in dem Bewegung erkannt wurde. Das ist explizit getestet
(`test_baseline_lernt_bewegung_nicht_als_normalzustand`).

### Mehrheitsentscheid statt Einzelschwelle

Ein einzelnes Gerät produziert regelmäßig Signale, die von Bewegung nicht zu
unterscheiden sind: Firmware-Update, Stromsparmodus, Kanalwechsel. Ein Mensch
stört dagegen **mehrere** Strecken gleichzeitig.

Deshalb verlangt `min_links` standardmäßig zwei gleichzeitig ausschlagende
Strecken. Die Ausnahme für einen einzelnen sehr starken Ausschlag greift nur,
wenn ohnehin weniger Strecken zur Verfügung stehen – sonst wäre die Bedingung in
einer kleinen Wohnung nie erfüllbar. Diese Einschränkung war eine bewusste
Korrektur: In der ersten Fassung genügte ein starker Einzelausschlag immer, was
in der Simulation prompt zu Fehlalarmen führte.

### Aufzeichnung und Wiedergabe

Schwellen an einem Verfahren zu justieren, das nur alle paar Stunden einen
Fehlalarm produziert, ist sonst eine Sache von Wochen. Mit `record` und `replay`
wird daraus ein Sekundenzyklus: einmal eine Nacht mitschneiden, danach beliebig
oft mit geänderten Parametern durchrechnen.

Dasselbe Format speist die Tests.

### Benachrichtigungen im eigenen Thread

Ein hängender HTTP-Aufruf – ntfy nicht erreichbar, SMTP-Server im Timeout – darf
die Messschleife nicht anhalten. Sonst reißt die Messreihe ab und der Detektor
verliert seine Fensterwerte. Der `NotificationHub` nimmt Ereignisse in eine
Warteschlange und stellt sie in einem Hintergrundthread zu; Fehler eines Kanals
bleiben lokal.

### Webserver aus der Standardbibliothek

Für fünf Endpunkte lohnt kein Framework, und auf einem Raspberry Pi zählt jede
vermiedene Abhängigkeit. `ThreadingHTTPServer` genügt.

### Kein numpy

Median, MAD und Standardabweichung über zehn bis dreißig Werte rechnet `statistics`
aus der Standardbibliothek schnell genug. numpy auf einem Pi zu installieren ist
mehr Aufwand als der Nutzen.

## Abhängigkeiten

| Paket | Wofür | Pflicht |
|---|---|---|
| `fritzconnection` | TR-064-Anbindung | ja |
| `requests` | HTTP für ntfy und Webhooks | ja |
| `PyYAML` | Konfiguration | ja |
| `paho-mqtt` | MQTT / Home Assistant | nein |
| `pytest` | Tests | nein |

## Nebenläufigkeit

Drei Threads:

1. **Hauptthread** – die Messschleife in `Engine.run`.
2. **Webserver** – `ThreadingHTTPServer`, ruft `Engine.set_mode` und
   `Engine.snapshot` auf. Beide sind über ein `RLock` abgesichert.
3. **Benachrichtigungen** – arbeitet die Warteschlange ab.

MQTT bringt seinen eigenen Netzwerkthread mit; Schaltbefehle laufen über
denselben abgesicherten `Engine.set_mode`.

## Tests

216 Tests, alle ohne FRITZ!Box lauffähig.

* **Synthetische Quelle** (`sources/synthetic.py`) erzeugt reproduzierbare
  Messreihen: konstanter RSSI mit Rauschen und langsamer Drift, überlagert von
  Bewegungsphasen. Damit lassen sich Aussagen wie „löst in 30 Minuten Ruhe nicht
  aus" und „erkennt Bewegung binnen 15 Sekunden" tatsächlich prüfen.
* **Mesh-Fixture** (`tests/data/mesh_sample.json`) bildet eine reale Topologie
  nach: Box, Repeater, Clients auf drei Bändern, eine getrennte Verbindung, eine
  doppelt gelistete Strecke.
* **Gefälschte TR-064-Verbindung** für die FRITZ!Box-Anbindung.
* **Echter HTTP-Server** auf Port 0 für die REST-Tests.

## Plattformunabhängigkeit

Getestet wird unter Linux; der Code ist so geschrieben, dass er unter
Windows 11 und macOS ebenso läuft:

* Pfade durchgängig über `pathlib`, Dateizugriffe immer mit `encoding="utf-8"`.
* Die Konsolenausgabe wird beim Start auf UTF-8 umgestellt – die klassische
  Windows-Konsole läuft sonst je nach Systemeinstellung auf cp850 und bricht
  bei Sonderzeichen ab.
* Keine Symlinks im Repository (Git legt dafür unter Windows ohne
  Entwicklermodus nur eine Textdatei an).
* `SIGTERM` wird nur registriert, wo es die Plattform zulässt.

`tests/test_portabilitaet.py` prüft diese vier Punkte dauerhaft mit.
