# Grenzen, Datenschutz und rechtliche Einordnung

## Was das Verfahren technisch nicht kann

**Es ortet nicht.** Erkannt wird, dass sich auf *irgendeiner* der überwachten
Funkstrecken etwas bewegt hat. Wo genau, in welche Richtung, wie viele Personen –
all das liefert RSSI nicht. Zonen sind eine Zuordnung von Geräten zu Räumen, die
man selbst vornimmt, keine Messung.

**Es erkennt keine stillstehenden Personen.** Wer sich hinsetzt und ruhig bleibt,
verschwindet. Für Atmungserkennung, wie sie die Forschung mit CSI zeigt, fehlen
sowohl die Messgröße als auch die Abtastrate um zwei Größenordnungen.

**Es unterscheidet nicht zwischen Mensch, Katze und Staubsaugerroboter.** Eine
Katze stört eine 5-GHz-Strecke messbar. Dagegen hilft Geometrie (Strecken höher
legen), kein Schwellwert.

**Es sieht nicht durch die Wohnung, aber manchmal durch die Wand.** Bewegung
hinter einer dünnen Wand kann durchschlagen. Wenn nachts regelmäßig zur selben
Zeit etwas auslöst, sind meist die Nachbarn die Ursache.

**Es ist deutlich gröber als Comcasts Lösung.** Die FRITZ!Box gibt keine
Kanalzustandsinformation nach außen; verfügbar sind RSSI, Datenrate und MCS im
Zwei-Sekunden-Takt. Das reicht für gehende Personen, nicht für mehr.

**Es lässt sich austricksen.** Wer die Funkstrecken kennt, kann sie umgehen.
Wer das WLAN stört oder den Strom abstellt, legt die Anlage lahm – WLANalarm
meldet dann Messfehler, aber niemand hört sie, wenn niemand hinschaut.

## Was die Anlage nicht ist

WLANalarm ist eine Selbstbaulösung. Es ist **keine Einbruchmeldeanlage** im
Sinne der VdS-Richtlinien oder der DIN EN 50131, sondern ein Eigenbauprojekt mit
allen zugehörigen Eigenschaften: keine Sabotageüberwachung, keine
Notstromversorgung, keine überwachte Übertragung, keine Zertifizierung.

Für Versicherungsfragen ist sie damit unerheblich. Wer einen anerkannten
Einbruchschutz braucht, kommt an einer zertifizierten Anlage nicht vorbei – und
davor an ordentlichen Schlössern und Fenstersicherungen, die statistisch weit
mehr bringen als jede Meldetechnik.

Sinnvoll ist WLANalarm als *zusätzliche* Meldeebene: Es kostet nichts außer
Strom, nutzt Geräte, die ohnehin da sind, und meldet, wenn sich in der leeren
Wohnung etwas bewegt.

## Datenschutz

### Was das Programm speichert

| Datum | Wo | Aufbewahrung |
|---|---|---|
| Ereignisse (Zeit, Typ, Zone, Score) | `state/wlanalarm.sqlite3` | 90 Tage, einstellbar |
| Baselines je Funkstrecke | dieselbe Datei | bis zur Neukalibrierung |
| Aktueller Modus | dieselbe Datei | dauerhaft |
| Rohmesswerte (RSSI-Reihen) | `state/recordings/*.ndjson` | **nur wenn eingeschaltet**, 3 Tage |

Keine Bilder, kein Ton, keine Inhaltsdaten. Gespeichert werden MAC-Adressen und
Gerätenamen der als Sensor verwendeten Geräte – die stehen ohnehin in der
FRITZ!Box.

### Was nach außen geht

Nichts, außer den Benachrichtigungen, die man selbst einrichtet. Es gibt keine
Cloud, keine Telemetrie, keinen Aufruf nach Hause.

Der bemerkenswerte Unterschied zu Comcast: Dort behält sich der Anbieter laut
Berichterstattung vor, die aus WiFi Motion gewonnenen Informationen an Dritte
weiterzugeben, ohne erneut zu informieren. Wer ntfy.sh nutzt, gibt Alarmtexte an
den Betreiber – dagegen hilft eine eigene ntfy-Instanz oder ein anderer Kanal.

### Wenn andere Menschen betroffen sind

Das Verfahren erfasst die Bewegung **aller** Personen in Reichweite, nicht nur
die von Einbrechern. Daraus folgt:

**In der eigenen Wohnung, allein lebend:** unproblematisch.

**In einer Wohngemeinschaft oder Familie:** Alle Bewohner sollten Bescheid
wissen. Eine heimliche Bewegungsaufzeichnung über Mitbewohner ist ein Eingriff
in deren Persönlichkeitsrecht – die Frage „wann war jemand zu Hause und wann
nicht" ist genau die Art von Datum, die man nicht heimlich über andere sammelt.

**Bei Angestellten, Reinigungskräften, Pflegediensten:** Eine
Verhaltenskontrolle von Beschäftigten ist arbeitsrechtlich eng begrenzt und ohne
Mitbestimmung regelmäßig unzulässig. Hier vorher informieren und klären.

**Bei Mietern oder Gästen:** Deren Bewegungen zu protokollieren, ohne dass sie
davon wissen, ist nicht zulässig.

**Nachbarn:** Die Erfassung reicht unvermeidlich ein Stück über die eigenen
Wände hinaus – das lässt sich technisch nicht sauber begrenzen. Ein Grund mehr,
Rohdatenaufzeichnung (`storage.record_samples`) nur zum Justieren einzuschalten
und danach wieder abzuschalten.

Für den rein privaten Gebrauch in den eigenen vier Wänden greift die
Haushaltsausnahme der DSGVO (Art. 2 Abs. 2 lit. c). Sobald Dritte systematisch
erfasst werden – Beschäftigte, Mieter – gilt das nicht mehr.

Dies ist eine Einordnung nach bestem Wissen, keine Rechtsberatung.

## Sicherheit der Installation

**Das FRITZ!Box-Kennwort liegt im Klartext** auf dem Rechner, der WLANalarm
ausführt – in der Konfigurationsdatei oder in der Umgebung. Deshalb: ein eigener
FRITZ!Box-Benutzer nur mit *FRITZ!Box Einstellungen* und ohne Internetzugriff.
Geht der Rechner verloren, sperrt man diesen einen Benutzer.

**Das Dashboard darf nicht offen im Netz stehen.** Wer `/api/mode` erreicht,
kann die Anlage entschärfen. Voreingestellt lauscht der Server nur auf
`127.0.0.1`; bei anderen Adressen erzwingt die Konfiguration ein Token.

**Nicht ins Internet weiterleiten.** Kein Portforwarding auf 8723. Wer von außen
herankommen will, nimmt VPN – die FRITZ!Box bringt WireGuard mit.

**Die Anlage meldet nicht, wenn sie ausfällt.** Ein Stromausfall am Raspberry Pi
oder ein Netzwerkproblem beendet die Überwachung stillschweigend. Wer sich
darauf verlässt, sollte `/api/health` überwachen – etwa mit Uptime Kuma oder
einem Cron-Job, der bei ausbleibender Antwort meldet.
