# WLANalarm

Bewegungserkennung über die WLAN-Funkstrecken einer AVM FRITZ!Box – ohne Kamera,
ohne zusätzliche Sensoren, ohne Cloud-Dienst.

## Funktionsweise

Jede WLAN-Verbindung zwischen der FRITZ!Box und einem ortsfesten Gerät dient als
virtueller Bewegungsmelder. Bewegt sich eine Person durch die Verbindungsstrecke,
verändert ihr Körper die Mehrwegeausbreitung des Funksignals. Empfangsfeldstärke
und ausgehandelte Datenrate ändern sich dadurch messbar. WLANalarm wertet diese
Änderungen aus und meldet Bewegung.

Das Verfahren entspricht dem Prinzip, das Comcast im August 2026 unter dem Namen
*WiFi Motion* für seine Xfinity-Gateways eingeführt hat
([heise-Meldung](https://heise.de/-11419315)).

```
   FRITZ!Box                                ortsfeste Geräte
        ╔═══╗                                    ┌──────────┐
        ║   ║~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~▶│ Fernseher│
        ║   ║~~~~~~~~~~~~~~ 🚶 ~~~~~~~~~~~~~~~~~▶│ Lautspr. │  ← gestörte Strecke
        ╚═══╝~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~▶│ Repeater │
                                                 └──────────┘
```

> **Schritt-für-Schritt-Anleitungen:**
> **[Windows 11](docs/windows-11.md)** ·
> **[Linux und Raspberry Pi](docs/linux-raspberry-pi.md)**
> Beide führen von der Installation bis zur laufenden Anlage.

## Leistungsumfang und Grenzen

Comcast wertet auf dem Gateway die Kanalzustandsinformation
(CSI) aus – Amplitude und Phase je einzelner OFDM-Unterträger, viele Male pro
Sekunde. Die FRITZ!Box gibt CSI nicht nach außen. Verfügbar sind über TR-064 nur
RSSI in dBm, Datenrate, MCS und Streamzahl, und das etwa alle zwei Sekunden.

Was daraus wird:

| | Comcast WiFi Motion | WLANalarm |
|---|---|---|
| Messgröße | CSI (Unterträger, Phase) | RSSI, Datenrate, MCS |
| Abtastrate | ~10–100 Hz | ~0,5 Hz |
| Erkennt Bewegung | ja, auch feine | ja, gehende Personen |
| Erkennt Atmung/Gesten | ja (in Forschung) | **nein** |
| Ortet Bewegung im Raum | grob | **nein**, nur je Funkstrecke/Zone |
| Läuft über | Comcast-Cloud | vollständig lokal |
| Daten an Dritte | [laut Comcast möglich](https://heise.de/-11419315) | keine |

**Erkannt wird** eine Person, die durch die Wohnung geht, zwischen Räumen
wechselt oder sich in der Nähe einer Funkstrecke bewegt – vorausgesetzt, ihr
Weg kreuzt tatsächlich eine der Funkstrecken. Ob das der Fall ist, entscheidet
die Aufstellung der Geräte, nicht das Verfahren.

**Nicht erkannt wird** eine Person, die sich ruhig verhält. Ebenso wenig lässt
sich bestimmen, wo genau im Raum eine Bewegung stattfindet oder wie viele
Personen anwesend sind.

WLANalarm ist eine Anwesenheitserkennung, kein Beweismittel und keine
zertifizierte Einbruchmeldeanlage.

**Die Anlage meldet ihren eigenen Ausfall nicht von selbst.** Ein Stromausfall
am Rechner oder eine Störung im Netzwerk beendet die Überwachung stillschweigend.
Wer sich darauf verlässt, sollte den Endpunkt `/api/health` überwachen – er
prüft, ob tatsächlich noch gemessen wird. Beschrieben in
[docs/linux-raspberry-pi.md](docs/linux-raspberry-pi.md#8-ausfall-überwachen).

Die vollständige Einordnung steht in
[docs/grenzen-und-datenschutz.md](docs/grenzen-und-datenschutz.md).

## Voraussetzungen

* Eine FRITZ!Box mit Mesh-Unterstützung (FRITZ!OS 7 oder neuer). Die
  **FRITZ!Box 5690 Pro** ist mit ihren drei Bändern (2,4 / 5 / 6 GHz) besonders
  gut geeignet, weil 6 GHz kurzwelliger und damit empfindlicher ist.
* Mindestens zwei **stationäre**, dauerhaft eingeschaltete WLAN-Geräte –
  Fernseher, Lautsprecher, Drucker, Kamera, Luftreiniger, Smart-Home-Zentrale.
  Ein per **WLAN** angebundener FRITZ!Repeater ist zusätzlich die stabilste
  Sensorstrecke überhaupt; hängt er am LAN-Kabel, dient er als weiterer
  Zugangspunkt für die Strecken zu seinen Clients.
* Python 3.11 oder neuer auf einem Rechner, der dauerhaft läuft
  (Raspberry Pi genügt).

## Installation

**Linux / macOS**

```bash
git clone https://github.com/ullrichc/WLANalarm.git
cd WLANalarm
python3 -m venv venv && source venv/bin/activate
pip install -e ".[mqtt]"          # ohne [mqtt], wenn kein Home Assistant
```

**Windows 11** – vollständig per Einrichtungsskript:

```powershell
irm https://raw.githubusercontent.com/ullrichc/WLANalarm/main/deploy/setup-windows.ps1 -OutFile setup-wlanalarm.ps1
.\setup-wlanalarm.ps1                          # Vorgabe: C:\Code\WLAN
```

Das Skript prüft die Voraussetzungen, holt das Projekt, legt die virtuelle
Umgebung an, installiert alles, führt die Testsuite aus und erzeugt eine
Konfigurationsvorlage. Ein erneuter Aufruf aktualisiert die Installation, ohne
die eigene `config.yaml` anzutasten. Anderes Ziel:
`.\setup-wlanalarm.ps1 -Zielpfad D:\Projekte\WLANalarm`

Oder von Hand:

```powershell
git clone https://github.com/ullrichc/WLANalarm.git C:\Code\WLAN
cd C:\Code\WLAN
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e ".[mqtt]"
```

Voraussetzungen: Git und Python 3.11+. Falls nicht vorhanden:
`winget install --id Git.Git` bzw. `winget install --id Python.Python.3.12`
(danach ein neues PowerShell-Fenster öffnen).

Falls PowerShell die Ausführung verweigert, einmalig:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### FRITZ!Box vorbereiten

1. **Benutzer anlegen:** FRITZ!Box-Oberfläche → *System* → *FRITZ!Box-Benutzer*
   → *Benutzer hinzufügen*. Name z. B. `wlanalarm`, Berechtigung
   **„FRITZ!Box Einstellungen"** anhaken. Kein Internetzugriff nötig.
2. **Schnittstelle freigeben:** *Heimnetz* → *Netzwerk* → *Netzwerkeinstellungen*
   → **„Zugriff für Anwendungen zulassen"** aktivieren.

Ausführlich mit Fehlerbildern: [docs/fritzbox-einrichtung.md](docs/fritzbox-einrichtung.md)
(unter Windows: [docs/windows-11.md](docs/windows-11.md), dort ist derselbe
Schritt in den Gesamtablauf eingebettet)

### Einrichten

```bash
wlanalarm init-config config.yaml     # schlanke Vorlage (--full für alle Einstellungen)
$EDITOR config.yaml                   # Benutzername eintragen
export FRITZ_PASSWORD='...'           # Kennwort des angelegten Benutzers
wlanalarm check                       # Verbindung und Konfiguration prüfen
```

Unter Windows setzt man das Kennwort so:

```powershell
$env:FRITZ_PASSWORD = '...'           # gilt für dieses Fenster
# dauerhaft für den eigenen Benutzer:
[Environment]::SetEnvironmentVariable('FRITZ_PASSWORD', '...', 'User')
```

`check` meldet Modell, FRITZ!OS-Version und die Zahl der nutzbaren Funkstrecken.

### Sensoren aussuchen

```bash
wlanalarm discover
```

```
5 Funkstrecken an fritz.box, Repeater Flur:

    Gerät                  MAC                Band        RSSI Zone       Begründung
------------------------------------------------------------------------------------
 ok Fernseher              AA:BB:CC:00:00:03  5 GHz    -58 dBm default    ortsfestes Gerät
 ok Kamera Eingang         AA:BB:CC:00:00:07  5 GHz    -61 dBm default    ortsfestes Gerät
 ok Repeater Flur          34:31:C4:00:00:20  5 GHz    -47 dBm default    Mesh-Knoten
 ok Sonos Wohnzimmer       AA:BB:CC:00:00:04  6 GHz    -52 dBm default    ortsfestes Gerät
 ok Steckdose Kueche       AA:BB:CC:00:00:05  2.4 GHz  -66 dBm default    ortsfestes Gerät

5 Strecke(n) werden als Sensor verwendet.
Als Nächstes: wlanalarm calibrate --minutes 10 (dabei die Wohnung nicht betreten)
```

Smartphones und Notebooks werden bewusst aussortiert – sie wandern mit ihren
Besitzern durch die Wohnung und legen sich schlafen, was der Detektor nicht von
Bewegung unterscheiden kann. Welches Gerät wohin gehört, klärt
[docs/aufbau-und-platzierung.md](docs/aufbau-und-platzierung.md).

### Kalibrieren

Der wichtigste Schritt. **Verlassen Sie dafür die Wohnung.** Während der
Messung darf sich niemand darin bewegen, sonst lernt die Anlage Bewegung als
Normalzustand.

```bash
wlanalarm calibrate --minutes 15
```

```
Gerät                    Band        RSSI    Ruhe  Streu  Sicht   Neu  Bewertung
-------------------------------------------------------------------------------------
Repeater Flur            5 GHz    -47 dBm   0.85dB  0.22   100%   94%  sehr gut - sehr ruhige Strecke
Sonos Wohnzimmer         6 GHz    -52 dBm   1.08dB  0.30   100%   91%  sehr gut - sehr ruhige Strecke
Fernseher                5 GHz    -58 dBm   1.60dB  0.66   100%   88%  gut - brauchbar ruhig
Steckdose Kueche         2.4 GHz  -66 dBm   0.00dB  0.00   100%    2%  ungeeignet - der Messwert erneuert
                                                                        sich nur in 2% der Messungen
Kamera Eingang           5 GHz    -74 dBm   7.91dB  2.22    98%   96%  ungeeignet - zu unruhig

  Sicht = wie oft das Gerät überhaupt in der Liste stand
  Neu   = wie oft die FRITZ!Box einen neuen Messwert lieferte
```

WLANalarm lernt daraus für **jede Strecke einzeln**, was ihr Normalzustand ist,
und schlägt Verbesserungen vor. Ungeeignete Strecken werden automatisch ausgeschlossen.

### Starten

```bash
wlanalarm run
```

Das Dashboard erreichen Sie unter <http://127.0.0.1:8723/>:

![Das Dashboard zeigt den Zustand der Anlage, die Betriebsarten, eine
Verlaufskurve des Bewegungsscores, eine Tabelle aller Funkstrecken mit Signal
und Ausschlag sowie die letzten Ereignisse.](docs/bilder/dashboard.png)

Es erklärt die angezeigten Werte selbst – was gerade gemessen wird, welche
Geräte als Bewegungsmelder dienen, welche nicht und warum.

## Bedienung

```bash
wlanalarm status              # Zustand des laufenden Dienstes
wlanalarm arm armed_away      # scharf schalten
wlanalarm disarm              # entschärfen
wlanalarm monitor             # Live-Anzeige im Terminal, ohne Alarm
```

### Betriebsarten

| Modus | Gedacht für | überwachte Zonen |
|---|---|---|
| `disarmed` | Anwesend, nichts soll auslösen | – |
| `armed_home` | Anwesend, nur Außenbereiche | `alarm.zones_armed_home` |
| `armed_night` | Nachtwache | `alarm.zones_armed_night` |
| `armed_away` | Niemand zu Hause | `alarm.zones_armed_away` |

Ein- und Ausgangsverzögerung funktionieren wie bei einer klassischen
Alarmanlage: Beim Scharfschalten bleiben 45 Sekunden zum Verlassen der Wohnung,
beim Auslösen 30 Sekunden zum Entschärfen, bevor der Alarm rausgeht.

Automatik gibt es in zwei Ausprägungen – Zeitplan (`alarm.schedule`, die
„Nachtwache") und Anwesenheit (`alarm.presence`, schaltet scharf, sobald alle
Bewohnerhandys das WLAN verlassen haben).

## Fehlalarme

Grundprinzip der Auslegung: **eine einzelne Funkstrecke genügt nie**. Standardmäßig
müssen mindestens zwei Strecken gleichzeitig ausschlagen. Ein Gerät, das ein Firmware-Update lädt
oder in den Stromsparmodus wechselt, bleibt so ohne Wirkung, weil die
übrigen Strecken ruhig bleiben.

Falls dennoch Fehlalarme auftreten:

```bash
# einmal eine Nacht mitschneiden (storage.record_samples: true)
wlanalarm replay state/recordings/samples-2026-08-19.ndjson
```

Das rechnet die Aufzeichnung mit der aktuellen Konfiguration durch und zeigt,
wie viele Fehlalarme pro Stunde entstünden. Geänderte Schwellenwerte lassen
sich damit in Sekunden prüfen statt über mehrere Nächte. Das komplette Vorgehen samt Wirkung jeder
einzelnen Stellschraube:
[docs/kalibrierung-und-tuning.md](docs/kalibrierung-und-tuning.md).

## Benachrichtigungen

Push aufs Handy (ntfy), E-Mail (SMTP), generischer Webhook und MQTT. Über MQTT
legt WLANalarm in Home Assistant per Discovery automatisch einen Bewegungsmelder
und eine schaltbare Alarmanlage an. Beispiele in
[docs/integrationen.md](docs/integrationen.md).

```bash
wlanalarm test-notify --level alarm     # alle Kanäle einmal durchprobieren
```

## Dauerbetrieb

| System | Datei | Hinweis |
|---|---|---|
| Linux, Raspberry Pi | [docs/linux-raspberry-pi.md](docs/linux-raspberry-pi.md) | vollständige Anleitung bis zum abgesicherten systemd-Dienst |
| Docker | `deploy/Dockerfile`, `deploy/docker-compose.yml` | |
| Windows 11 | `deploy/windows-autostart.ps1` | richtet eine Aufgabe in der Aufgabenplanung ein |
| Windows 11 (Einrichtung) | `deploy/setup-windows.ps1` | Erstinstallation und Aktualisierung |

Unter Windows in einer PowerShell **mit Administratorrechten**:

```powershell
.\deploy\windows-autostart.ps1 -ProjektPfad C:\WLANalarm `
    -FritzPasswort (Read-Host -AsSecureString)
```

Die Aufgabe startet beim Hochfahren, läuft ohne angemeldeten Benutzer und
startet nach einem Absturz von selbst neu.


## Aufbau des Projekts

```
src/wlanalarm/
  model.py        Datenmodell einer Funkstrecke
  config.py       Konfiguration mit Validierung
  sources/        FRITZ!Box-Anbindung (Mesh-Liste, TR-064, Wiedergabe, Simulation)
  selection.py    welche Strecken taugen als Sensor
  baseline.py     robuste Statistik (Median/MAD)
  detector.py     Bewegungserkennung
  alarm.py        Zustandsautomat der Alarmanlage
  engine.py       Hauptschleife
  calibrate.py    Vermessung der Ruhephase
  notify/         Benachrichtigungskanäle
  web/            Dashboard und REST-Schnittstelle
```

Hintergründe zu den Entwurfsentscheidungen:
[docs/architektur.md](docs/architektur.md).

## Entwicklung

```bash
pip install -e ".[dev,mqtt]"
pytest
```

Die Tests laufen ohne FRITZ!Box: eine synthetische Quelle erzeugt realistische
Messreihen mit Rausch- und Bewegungsphasen, sodass sich das Verhalten des
Detektors – erkennt Bewegung, ignoriert einzelne auffällige Geräte, lernt Bewegung
nicht als Normalzustand – reproduzierbar prüfen lässt.

## Lizenz

Apache-2.0, siehe [LICENSE](LICENSE).

WLANalarm ist ein Eigenbauprojekt und kein zertifiziertes Sicherheitsprodukt.
Es ersetzt weder eine Einbruchmeldeanlage nach VdS noch mechanische
Sicherungen an Türen und Fenstern.
