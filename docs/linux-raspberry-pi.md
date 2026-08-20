# WLANalarm auf Linux und Raspberry Pi betreiben

Diese Anleitung führt von der Installation bis zum abgesicherten Dienst unter
systemd. Getestet gegen Debian 12 (Bookworm) und Raspberry Pi OS Bookworm; auf
anderen Distributionen unterscheiden sich nur die Paketnamen.

## Voraussetzungen

* **Python 3.11 oder neuer.** Debian 12 und Raspberry Pi OS Bookworm bringen
  3.11 mit. **Debian 11 (Bullseye) hat nur 3.9 und genügt nicht** – dort
  entweder das System aktualisieren oder einen Container verwenden.
  ```bash
  python3 --version
  ```
* **Ein Rechner, der durchläuft.** Ein Raspberry Pi 3 oder neuer reicht
  reichlich; der Dienst braucht im Betrieb weniger als 60 MB Arbeitsspeicher
  und belastet einen Pi-4-Kern zu wenigen Prozent.
* **Zwei ortsfeste WLAN-Geräte, die häufig funken.** Warum das die schärfste
  Bedingung ist, steht in
  [aufbau-und-platzierung.md](aufbau-und-platzierung.md).

## 1. Systempakete

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git
```

`python3-venv` fehlt auf Debian-Systemen standardmäßig; ohne das Paket bricht
das Anlegen der virtuellen Umgebung mit einer irreführenden Meldung ab.

## 2. FRITZ!Box vorbereiten

Einen eigenen Benutzer anlegen und den Anwendungszugriff freischalten – die
beiden Schritte sind in [fritzbox-einrichtung.md](fritzbox-einrichtung.md)
ausführlich beschrieben.

## 3. Zum Ausprobieren: Installation im eigenen Konto

```bash
git clone https://github.com/ullrichc/WLANalarm.git
cd WLANalarm
python3 -m venv venv
source venv/bin/activate
pip install -e ".[mqtt]"          # ohne [mqtt], wenn kein Home Assistant

wlanalarm init-config config.yaml
$EDITOR config.yaml               # Benutzernamen eintragen
export FRITZ_PASSWORD='...'
wlanalarm check
```

Das genügt, um das Verfahren kennenzulernen. Für den Dauerbetrieb ist diese
Installation **nicht** geeignet: Die systemd-Unit setzt `ProtectHome=true`,
womit der Dienst nicht mehr auf Ihr Benutzerverzeichnis zugreifen kann.

## 4. Dauerbetrieb: Installation unter /opt

Die Unit erwartet einen eigenen Dienstbenutzer und feste Verzeichnisse. Alle
Befehle als Administrator:

```bash
# Dienstbenutzer ohne Anmeldemöglichkeit
sudo useradd --system --no-create-home --shell /usr/sbin/nologin wlanalarm

# Verzeichnisse
sudo mkdir -p /opt/wlanalarm /etc/wlanalarm /var/lib/wlanalarm

# Quellen nach /opt holen
sudo git clone https://github.com/ullrichc/WLANalarm.git /opt/wlanalarm/src

# Virtuelle Umgebung und Installation
sudo python3 -m venv /opt/wlanalarm/venv
sudo /opt/wlanalarm/venv/bin/pip install --upgrade pip
sudo /opt/wlanalarm/venv/bin/pip install "/opt/wlanalarm/src[mqtt]"
```

Beachten Sie das fehlende `-e`: Für den Dienstbetrieb wird **fest installiert**,
nicht editierbar. Eine editierbare Installation verweist auf den Quellbaum, was
mit `ProtectHome=true` und `ProtectSystem=strict` in Konflikt gerät.

**Konfiguration anlegen:**

```bash
sudo /opt/wlanalarm/venv/bin/wlanalarm init-config /etc/wlanalarm/config.yaml
sudo $EDITOR /etc/wlanalarm/config.yaml
```

Zwei Werte müssen für den Dienstbetrieb abweichen:

```yaml
fritzbox:
  username: "wlanalarm"
  password: "${env:FRITZ_PASSWORD}"

storage:
  # Absoluter Pfad. Mit dem voreingestellten "./state" landet der Zustand
  # dort, wo der Befehl gerade ausgeführt wird - beim Dienst und bei der
  # Kalibrierung an unterschiedlichen Orten.
  directory: "/var/lib/wlanalarm/state"
```

**Kennwort hinterlegen:**

```bash
printf 'FRITZ_PASSWORD=%s\n' 'IhrKennwort' | sudo tee /etc/wlanalarm/env >/dev/null
sudo chmod 600 /etc/wlanalarm/env
sudo chown root:root /etc/wlanalarm/env
```

**Rechte setzen:**

```bash
sudo chown -R wlanalarm:wlanalarm /var/lib/wlanalarm
sudo chown root:wlanalarm /etc/wlanalarm/config.yaml /etc/wlanalarm/env
sudo chmod 640 /etc/wlanalarm/config.yaml /etc/wlanalarm/env
```

## 5. Kalibrieren – als Dienstbenutzer

> **Der häufigste Fehler dieses Aufbaus:** Wer als eigener Benutzer kalibriert,
> schreibt die Baselines in ein anderes Verzeichnis, als der Dienst später
> liest. Das Programm meldet dann beim Start „Keine Kalibrierung gefunden",
> ohne dass ein Fehler sichtbar wäre.

Deshalb ausdrücklich als Dienstbenutzer und mit derselben Konfiguration:

```bash
sudo -u wlanalarm /opt/wlanalarm/venv/bin/wlanalarm \
     calibrate -c /etc/wlanalarm/config.yaml --minutes 15
```

Das Kennwort muss dafür in der Sitzung stehen:

```bash
sudo -u wlanalarm env FRITZ_PASSWORD="$(sudo sed -n 's/^FRITZ_PASSWORD=//p' /etc/wlanalarm/env)" \
     /opt/wlanalarm/venv/bin/wlanalarm calibrate -c /etc/wlanalarm/config.yaml --minutes 15
```

Während der Messung darf sich niemand in der Wohnung bewegen.

## 6. Dienst einrichten

```bash
sudo cp /opt/wlanalarm/src/deploy/wlanalarm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wlanalarm
systemctl status wlanalarm
```

Protokoll mitlesen:

```bash
journalctl -u wlanalarm -f
```

## 7. Dashboard auf einem Rechner ohne Bildschirm

Voreingestellt lauscht der Webserver nur auf `127.0.0.1` – auf einem
Kleinstrechner ohne Bildschirm nutzt das nichts. Zwei Wege:

**a) SSH-Tunnel (empfohlen, nichts zu konfigurieren)**

```bash
ssh -L 8723:127.0.0.1:8723 pi@raspberrypi
```

Danach im eigenen Browser <http://127.0.0.1:8723/> öffnen.

**b) Im Heimnetz freigeben – nur mit Token**

```yaml
web:
  host: "0.0.0.0"
  port: 8723
  token: "${env:WEB_TOKEN}"
```

Das Token gehört zusätzlich in `/etc/wlanalarm/env`. Die Konfiguration lehnt
`0.0.0.0` ohne Token ab: Wer `/api/mode` erreicht, kann die Anlage entschärfen.

> Das Token lässt sich auch als `?token=…` an die Adresse hängen, damit das
> Dashboard im Browser funktioniert. Es steht dann allerdings im Browserverlauf.
> Wo das stört, ist der SSH-Tunnel die bessere Wahl.
>
> **Niemals per Portweiterleitung ins Internet freigeben.** Für den Zugriff von
> unterwegs bringt die FRITZ!Box WireGuard mit.

## 8. Ausfall überwachen

Eine Alarmanlage, die stillschweigend ausfällt, ist gefährlicher als keine.
`/api/health` prüft, ob tatsächlich noch gemessen wird, und antwortet mit
HTTP 503, sobald die Messung steht oder sich Fehler häufen:

```bash
curl -s localhost:8723/api/health | jq
```

```json
{
  "status": "ok",
  "reasons": [],
  "last_scan_age": 1.4,
  "consecutive_errors": 0,
  "ticks": 4213,
  "state": "armed_away"
}
```

Dieser Endpunkt verlangt kein Token, damit Überwachungswerkzeuge ihn erreichen.
Binden Sie ihn in Uptime Kuma, Zabbix oder einen Cron-Auftrag ein – oder
schlicht:

```bash
*/5 * * * * curl -fsS localhost:8723/api/health >/dev/null || \
            echo "WLANalarm antwortet nicht" | mail -s "WLANalarm" ich@example.org
```

Zusätzlich meldet systemd einen abgestürzten Dienst:

```bash
systemctl is-failed wlanalarm
```

## 9. Aktualisieren

```bash
sudo git -C /opt/wlanalarm/src pull
sudo /opt/wlanalarm/venv/bin/pip install --upgrade "/opt/wlanalarm/src[mqtt]"
sudo systemctl restart wlanalarm
```

Die Konfiguration unter `/etc/wlanalarm/` bleibt unangetastet.

## 10. Entfernen

```bash
sudo systemctl disable --now wlanalarm
sudo rm /etc/systemd/system/wlanalarm.service
sudo systemctl daemon-reload
sudo rm -rf /opt/wlanalarm /etc/wlanalarm /var/lib/wlanalarm
sudo userdel wlanalarm
```

## Ressourcenbedarf

| | Wert |
|---|---|
| Arbeitsspeicher | unter 60 MB |
| CPU auf einem Raspberry Pi 4 | wenige Prozent eines Kerns |
| Abfragen an die FRITZ!Box | eine alle zwei Sekunden |
| Ereignisdatenbank | wenige MB im Jahr |
| Rohaufzeichnung (`record_samples: true`) | **50 bis 150 MB pro Tag**, je nach Zahl der Sensorstrecken |

Die Rohaufzeichnung ist zum Nachjustieren gedacht und sollte danach wieder
abgeschaltet werden – auf einer SD-Karte fällt der Schreibaufwand ins Gewicht.
Voreingestellt ist sie aus.

## MQTT und Home Assistant

Siehe [integrationen.md](integrationen.md). Ein Hinweis zur Last: WLANalarm
veröffentlicht den Zustand bei jedem Messtakt, also etwa alle zwei Sekunden.
Da das Zustandsthema zugleich die Attributquelle des Bewegungsmelders ist,
schreibt der Home-Assistant-Recorder im selben Takt mit. Wer das nicht möchte,
schließt die veränderlichen Attribute in der Recorder-Konfiguration aus:

```yaml
recorder:
  exclude:
    entity_globs:
      - binary_sensor.wlanalarm_*
```

## Fehlerbehebung

**`python3 -m venv` bricht ab**
`sudo apt install python3-venv`

**Dienst startet nicht, `status` zeigt `code=exited, status=203/EXEC`**
Der Pfad in der Unit stimmt nicht mit der Installation überein. Prüfen:
`ls -l /opt/wlanalarm/venv/bin/wlanalarm`

**Dienst startet nicht, `EnvironmentFile` wird bemängelt**
`/etc/wlanalarm/env` fehlt oder ist für den Dienstbenutzer nicht lesbar.

**`ModuleNotFoundError: wlanalarm`**
Es wurde editierbar (`pip install -e`) ins Benutzerverzeichnis installiert.
Wegen `ProtectHome=true` findet der Dienst das Paket nicht. Siehe Schritt 4.

**„Keine Kalibrierung gefunden", obwohl kalibriert wurde**
Die Kalibrierung lief unter einem anderen Benutzer oder mit einer anderen
Konfiguration und hat in ein anderes `state`-Verzeichnis geschrieben. Siehe
Schritt 5. Prüfen:
```bash
sudo ls -l /var/lib/wlanalarm/state/
```

**`wlanalarm status` meldet einen Verbindungsfehler**
Der Befehl spricht über HTTP mit dem *laufenden* Dienst. Er funktioniert nur,
solange dieser läuft, und benötigt dieselbe Konfiguration:
```bash
/opt/wlanalarm/venv/bin/wlanalarm status -c /etc/wlanalarm/config.yaml
```

**`wlanalarm check` zeigt `FRITZ!Box : tr064 (…)` statt `mesh`**
Die Mesh-Übersicht war nicht abrufbar, und WLANalarm ist auf die deutlich
gröbere TR-064-Abfrage ausgewichen. Meist fehlt dem FRITZ!Box-Benutzer die
Berechtigung „FRITZ!Box Einstellungen". Der Grund steht im Protokoll:
```bash
journalctl -u wlanalarm | grep -i mesh
```
