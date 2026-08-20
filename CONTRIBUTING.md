# Mitwirken

Beiträge sind willkommen – Fehlerberichte ebenso wie Verbesserungen am Code
oder an der Dokumentation.

## Fehler melden

Bitte über die [Issues](https://github.com/ullrichc/WLANalarm/issues).

Bei Problemen mit der Erkennung oder mit der FRITZ!Box-Anbindung hilft der
Diagnosebericht am meisten:

```bash
wlanalarm diagnose -o diagnose.txt
```

Der Bericht ersetzt Gerätenamen, MAC-Adressen und SSIDs durch Platzhalter und
kann deshalb angehängt werden. Bitte prüfen Sie ihn vor dem Absenden trotzdem
kurz durch.

Nützlich sind außerdem:

* FRITZ!Box-Modell und FRITZ!OS-Version (steht in der Ausgabe von `wlanalarm check`)
* Betriebssystem und Python-Version
* die Ausgabe von `wlanalarm check` und `wlanalarm discover`

## Sicherheitslücken

Bitte **nicht** als öffentliches Issue melden, sondern wie in
[SECURITY.md](SECURITY.md) beschrieben.

## Änderungen einreichen

```bash
git clone https://github.com/ullrichc/WLANalarm.git
cd WLANalarm
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev,mqtt]"
pytest
```

Die Testsuite läuft ohne FRITZ!Box: Eine synthetische Messquelle erzeugt
reproduzierbare Messreihen, dazu kommen Fixtures echter Mesh-Antworten.

Vor einem Pull Request:

* `pytest` muss vollständig durchlaufen.
* Neues Verhalten braucht einen Test. Für gefundene Fehler bitte einen Test,
  der ohne die Korrektur fehlschlägt.
* `python -m pyflakes src/wlanalarm tests` sollte nichts melden.

## Hinweise zum Stil

* **Sprache:** Code, Kommentare, Dokumentation und Programmausgaben auf
  Deutsch. Sachlich und ohne Umgangssprache.
* **Kommentare erklären das Warum**, nicht das Was. Warum eine Schwelle so
  gewählt ist, warum ein Sonderfall behandelt wird – das steht sonst nirgends.
* **Konsolenausgaben** müssen sich in cp850 und cp1252 darstellen lassen; die
  klassische Windows-Konsole bricht sonst ab. Umlaute sind in Ordnung,
  typografische Zeichen wie „–" nicht. `tests/test_portabilitaet.py` prüft das.
* **Keine echten Gerätenamen, MAC-Adressen oder Netzwerknamen** in Code, Tests
  oder Dokumentation. Für Beispiele den Bereich `02:00:00:…` verwenden.
* **Abhängigkeiten sparsam.** Das Projekt soll auf einem Raspberry Pi ohne
  Übersetzungsvorgang installierbar bleiben.
