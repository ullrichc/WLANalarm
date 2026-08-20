# FRITZ!Box einrichten

WLANalarm braucht lesenden Zugriff auf zwei Dinge: die Geräteliste und die
Mesh-Topologie. Beides läuft über TR-064, die lokale Steuerschnittstelle der
FRITZ!Box. Es wird nichts an der Box verändert.

## 1. Benutzer anlegen

> *System* → *FRITZ!Box-Benutzer* → *Benutzer hinzufügen*

| Feld | Wert |
|---|---|
| Benutzername | `wlanalarm` |
| Kennwort | ein langes, eigenes Kennwort |
| Zugang aus dem Internet erlaubt | **aus** |
| FRITZ!Box Einstellungen | **an** |
| Sprachnachrichten, Faxe, FRITZ!App Fon | aus |
| NAS-Inhalte | aus |
| Smart-Home-Geräte | aus |

Die Berechtigung *FRITZ!Box Einstellungen* ist zwingend – ohne sie liefert die
Mesh-Liste HTTP 403.

Ein eigener Benutzer statt des Hauptkontos hat einen praktischen Grund: Das Kennwort
liegt im Klartext auf dem Rechner, der WLANalarm ausführt. Geht dieser Rechner
verloren, sperrt man diesen einen Benutzer und ist fertig.

## 2. Anwendungszugriff freigeben

> *Heimnetz* → *Netzwerk* → Reiter *Netzwerkeinstellungen*
> → **„Zugriff für Anwendungen zulassen"** anhaken

Ohne diesen Haken antwortet Port 49000 gar nicht.

## 3. Kennwort außerhalb der Konfigurationsdatei ablegen

```yaml
fritzbox:
  username: "wlanalarm"
  password: "${env:FRITZ_PASSWORD}"
```

```bash
export FRITZ_PASSWORD='...'          # interaktiv
```

Im Dauerbetrieb kommt es aus `/etc/wlanalarm/env` (systemd `EnvironmentFile`,
Rechte 0600) oder aus der `.env`-Datei von Docker Compose.

## 4. Prüfen

```bash
wlanalarm check
```

```
Konfiguration config.yaml: in Ordnung
  Takt        : 2.0 s
  Fenster     : 12.0 s
  Baseline    : 15 min 0 s
  Kanäle      : 1
  FRITZ!Box   : mesh (FRITZ!Box 5690 Pro, FRITZ!OS 8.20)
  Funkstrecken: 12 gefunden, 5 nutzbar
```

## Fehlerbilder

**„Verbindung zur FRITZ!Box fehlgeschlagen"**
Port 49000 nicht erreichbar. Haken aus Schritt 2 prüfen. Bei abweichender
IP-Adresse `fritzbox.address` anpassen (`http://192.168.178.1`).

**„TR-064 antwortet nicht wie erwartet" / 401**
Falsches Kennwort oder falscher Benutzername. Zur Kontrolle:
`curl -s http://fritz.box:49000/tr64desc.xml | head` – kommt XML zurück, lebt
die Schnittstelle und es liegt an den Zugangsdaten.

**„Mesh-Liste lieferte HTTP 403" / „Device has no access to topology information"**
Dem Benutzer fehlt *FRITZ!Box Einstellungen*.

**„… bietet keine Mesh-Liste an"**
FRITZ!OS älter als 7. WLANalarm weicht bei `source: auto` selbsttätig auf
TR-064 aus – dann gibt es nur noch grobe Prozentwerte statt dBm, und Geräte
hinter einem Repeater sind unsichtbar. Ein FRITZ!OS-Update ist die bessere
Antwort.

**Alle Signalwerte sind leer**

```bash
wlanalarm diagnose
```

Der Bericht zeigt, welche Felder Ihre FRITZ!OS-Version verwendet und wo eine
Verbindung verloren geht. Er ersetzt Gerätenamen und MAC-Adressen durch
Platzhalter und lässt sich deshalb weitergeben.

Bekannter Fall: Ab **Mesh-Schema 8.x** (FRITZ!OS 8.2x, etwa auf der
FRITZ!Box 5690 Pro) heißt die Empfangsleistung nicht mehr `rssi`, sondern
`rx_rcpi`, dazu kommt der Störabstand `rx_rsni` – beides Bezeichnungen aus
IEEE 802.11k. WLANalarm liest beide Schreibweisen. Wenn Ihre Box ein noch
anderes Feld verwendet, schicken Sie den Diagnosebericht als Fehlermeldung ein.

## Was WLANalarm abfragt

| Aufruf | Zweck |
|---|---|
| `DeviceInfo:1 GetInfo` | Modell und FRITZ!OS-Version für die Diagnose |
| `Hosts:1 X_AVM-DE_GetMeshListPath` | Pfad zur Mesh-Topologie |
| GET auf diesen Pfad | RSSI, Datenrate, MCS je Funkstrecke |
| `Hosts:1 GetSpecificHostEntry` | nur bei aktivierter Anwesenheitserkennung |
| `WLANConfiguration:n …` | nur im TR-064-Ersatzmodus |

Alles lesend, alles im Heimnetz. Es geht nichts ins Internet, außer den
Benachrichtigungen, die man selbst einrichtet.
