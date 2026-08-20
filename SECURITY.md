# Sicherheitshinweise

## Eine Lücke melden

Sicherheitsrelevante Fehler bitte **nicht** als öffentliches Issue, sondern
über [GitHub Security Advisories](https://github.com/ullrichc/WLANalarm/security/advisories/new)
melden. Eine Rückmeldung erfolgt in der Regel innerhalb von zwei Wochen.

Dieses Projekt wird in der Freizeit gepflegt; es gibt keine zugesicherte
Reaktionszeit und keinen kommerziellen Support.

## Was das Programm angreifbar macht

**Das FRITZ!Box-Kennwort liegt im Klartext vor** – in der Konfigurationsdatei
oder in einer Umgebungsvariablen. Legen Sie dafür einen eigenen
FRITZ!Box-Benutzer an, der nur die Berechtigung „FRITZ!Box Einstellungen" hat
und keinen Internetzugriff. Geht der Rechner verloren, sperren Sie diesen einen
Benutzer.

**Wer die Weboberfläche erreicht, kann die Anlage entschärfen.** Voreingestellt
lauscht sie nur auf `127.0.0.1`. Bei jeder anderen Adresse erzwingt die
Konfiguration ein Token. Schreibende Zugriffe verlangen zusätzlich den
Inhaltstyp `application/json` und eine passende Herkunft, damit eine im selben
Browser geöffnete fremde Webseite die Anlage nicht schalten kann.

**Niemals per Portweiterleitung ins Internet freigeben.** Für den Zugriff von
unterwegs bringt die FRITZ!Box WireGuard mit.

**Die Anlage ist kein zertifiziertes Sicherheitsprodukt.** Sie hat keine
Sabotageüberwachung, keine Notstromversorgung und keine überwachte Übertragung.
Sie lässt sich durch Stören des WLANs oder Abschalten des Stroms außer Betrieb
setzen. Näheres in [docs/grenzen-und-datenschutz.md](docs/grenzen-und-datenschutz.md).

## Was das Programm nach außen gibt

Nichts – außer den Benachrichtigungen, die Sie selbst einrichten. Es gibt keine
Telemetrie und keinen Cloud-Dienst. Wer den öffentlichen Dienst ntfy.sh
verwendet, gibt die Alarmtexte an dessen Betreiber weiter.

## Unterstützte Versionen

Gepflegt wird jeweils der aktuelle Stand des Zweigs `main`.
