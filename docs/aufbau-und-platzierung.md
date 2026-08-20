# Aufbau und Platzierung

Das Verfahren steht und fällt mit der Frage, **wo die Funkstrecken verlaufen**.
Eine Funkstrecke ist die Luftlinie zwischen der FRITZ!Box (oder einem Repeater)
und einem stationären Gerät. Erkannt wird nur, was diese Linie kreuzt.

```
        ╔═══════╗                                   ┌─────────┐
        ║ FRITZ ║ ─────────── Funkstrecke ─────────▶│Fernseher│
        ║  !Box ║                    ▲              └─────────┘
        ╚═══════╝                    │
                              hier wird erkannt
```

Daraus folgt alles Weitere.

## Die Grundregel

**Die zu überwachenden Laufwege müssen zwischen der Box und den ausgewählten
Geräten liegen.** Wohnungstür, Flur, Treppe, Terrassentür: Wer dort etwas
mitbekommen will, braucht auf beiden Seiten dieser Stelle ein Gerät.

Falsch wäre, alle Geräte in einem Raum zu haben und darauf zu hoffen, dass die
Wohnungstür am anderen Ende der Wohnung auffällt.

## Welche Geräte taugen

| Sehr gut | Brauchbar | Untauglich |
|---|---|---|
| FRITZ!Repeater | Fernseher, Beamer | Smartphones |
| Smart-Speaker | Netzwerkdrucker | Notebooks, Tablets |
| Überwachungskamera | Smart-TV-Stick | Alles auf Akku |
| Smart-Home-Zentrale | WLAN-Steckdose | Geräte mit Nachtabschaltung |

Drei Anforderungen, alle drei müssen erfüllt sein:

1. **Steht still.** Ein Gerät, das bewegt wird, erzeugt genau das Signal, nach
   dem gesucht wird.
2. **Bleibt an.** Ein Drucker, der sich nachts abschaltet, reißt seine
   Funkstrecke ab – die Kalibrierung meldet das als „nur in 60 % der Messungen
   sichtbar".
3. **Funkt oft.** Das ist in der Praxis die schärfste Hürde – und die am
   leichtesten zu übersehende. Die FRITZ!Box erneuert den Messwert einer
   Verbindung nur, wenn dort auch Daten fließen. Ein Luftreiniger oder eine
   Steckdose sendet oft nur alle ein bis zwei Minuten ein Lebenszeichen;
   dazwischen liefert die Box unverändert denselben Wert.

   Ein eingefrorener Wert sieht aus wie ein vollkommen ruhiger Sensor, trägt
   aber keinerlei Information: Was sich in Ruhe nicht regt, regt sich auch bei
   Bewegung nicht. `wlanalarm calibrate` weist das in der Spalte **Neu** aus –
   sie zeigt, wie oft die Box tatsächlich einen neuen Wert lieferte. Unter
   10 % ist eine Strecke unbrauchbar, egal wie stabil sie wirkt.

   Zuverlässig oft funken: ein eingeschalteter Fernseher, ein Lautsprecher mit
   laufender Wiedergabe, eine Überwachungskamera mit Videostrom, ein per WLAN
   angebundener Repeater. Sparsame Sensorik dagegen fast nie.

## Repeater: es kommt auf die Anbindung an

Repeater helfen in beiden Fällen, aber auf völlig verschiedene Weise. Wie Ihre
angebunden sind, sehen Sie in der FRITZ!Box unter *Heimnetz* → *Mesh*.

**Per WLAN angebunden (Funk-Backhaul)**

Dann ist die Strecke Box↔Repeater selbst eine Sensorstrecke – und zwar die
beste, die es gibt: Beide Enden stehen fest, beide sind netzbetrieben, beide
funken permanent für den Mesh-Betrieb. Sie überbrückt zudem genau die Distanz,
die man überwachen will, weil Repeater dort stehen, wo die Wohnung breit ist.

**Per LAN angebunden (Kabel-Backhaul)**

Dann gibt es keine Funkstrecke zwischen Box und Repeater, und in der Liste
taucht auch keine auf. **Das ist kein Fehler.** Der Repeater ist trotzdem
wertvoll, nur anders: Er ist ein zusätzlicher Zugangspunkt, und die Strecken
von ihm zu seinen Clients werden ganz normal ausgewertet. In `wlanalarm
discover` erkennen Sie das daran, dass in der Kopfzeile neben `fritz.box` auch
`fritz.repeater` als Zugangspunkt steht.

Für die Erkennung ist das sogar günstiger: Weil der Rückweg über Kabel läuft,
bleibt die volle Funkkapazität für die Messstrecken übrig, und der Repeater
deckt einen Wohnungsteil ab, den die Box allein nicht erreicht.

> **Stellen Sie einen per LAN angebundenen Repeater nicht auf WLAN um**, nur
> um eine Sensorstrecke zu gewinnen. Sie verschlechtern damit Ihr Netz und
> gewinnen eine einzelne Strecke, die Sie ebenso gut über ein stationäres
> Gerät bekommen.

In beiden Fällen gilt: Was wirklich zählt, sind stationäre Geräte an den
richtigen Stellen. Ein Lautsprecher auf der anderen Seite des Flurs bringt mehr
als jede Repeater-Feinheit.

## Bandwahl

| Band | Wellenlänge | Eignung |
|---|---|---|
| 6 GHz | 5 cm | **am besten** – reagiert am feinsten, breite Kanäle |
| 5 GHz | 6 cm | sehr gut, Standardwahl |
| 2,4 GHz | 12,5 cm | schwach – langwellig, beugt sich um Menschen herum |

**2,4 GHz wird trotzdem verwendet.** Die Bandvorliebe steckt in der Rangfolge,
nicht in einem Filter: Muss WLANalarm die Zahl der Strecken begrenzen
(`selection.max_links`), fliegt 2,4 GHz zuerst raus – ausgeschlossen wird es
aber nicht. Der Grund ist praktischer Natur: In vielen Haushalten hängen
ausgerechnet die brauchbaren, dauerhaft aktiven Geräte – Steckdosen, Luftreiniger, Sensoren –
nur im 2,4-GHz-Netz, weil sie nichts anderes können. Vier stabile
2,4-GHz-Strecken schlagen zwei empfindliche, die niemand kreuzt.

Wer bewusst einschränken will:

```yaml
selection:
  bands: ["5 GHz", "6 GHz"]
```

Umgekehrt gilt: Kann eines Ihrer stationären Geräte 5 GHz, bringt der Umzug
dorthin spürbar mehr Empfindlichkeit als jede Einstellung.

## Beispiel: Dreizimmerwohnung

```
   ┌──────────────┬───────────────┬──────────────┐
   │ Schlafzimmer │     Flur      │  Wohnzimmer  │
   │              │               │              │
   │              │  [Repeater]   │  [TV] [Sonos]│
   │              │      │        │    ╲   ╱     │
   │              │  ════╪════════════════╪═══   │
   │              │  ┌───┴───┐    │       │      │
   │              │  │Wohnungs│   │  ╔═══════╗   │
   │              │  │  tür   │   │  ║FRITZ! ║   │
   └──────────────┴──┴───────┴────┴──╚═══════╝───┘
```

Die Box steht im Wohnzimmer, der Repeater im Flur. Die Strecke Box↔Repeater
kreuzt den Durchgang Wohnzimmer→Flur, die Strecken zu TV und Sonos decken das
Wohnzimmer ab. Wer durch die Wohnungstür kommt, stört zuerst die Repeaterstrecke
und wenig später die anderen beiden – genau das Muster, auf das die Anforderung
„mindestens zwei Strecken gleichzeitig" abzielt.

Zonen dazu:

```yaml
links:
  - mac: "..."   # Repeater
    name: "Repeater Flur"
    zone: "flur"
    weight: 1.5
  - mac: "..."   # TV
    zone: "wohnzimmer"
  - mac: "..."   # Sonos
    zone: "wohnzimmer"

alarm:
  zones_armed_home: ["flur"]                 # nachts nur der Eingangsbereich
  zones_armed_night: ["flur", "wohnzimmer"]
  zones_armed_away: []                       # abwesend: alles
```

## Praxishinweise

**Reichweite:** Unter etwa −70 dBm schwankt der RSSI von sich aus so stark, dass
er als Sensor kaum noch taugt. Die Kalibrierung weist darauf hin.

**Wände:** Eine Betonwand zwischen Box und Gerät dämpft stark – die Strecke wird
unempfindlich, weil ohnehin kaum noch Signal ankommt. Sichtverbindung oder
höchstens eine Leichtbauwand ist ideal.

**Haustiere:** Eine Katze stört eine 5-GHz-Strecke messbar. Wer Tiere hat,
sollte Strecken hoch legen (Regalbrett statt Fußboden) und `detector.min_links`
erhöhen – dagegen hilft kein Schwellwert, nur Geometrie.

**Nachbarn:** Bewegung hinter einer dünnen Wand kann durchschlagen. Wenn nachts
regelmäßig zur selben Zeit etwas auslöst, ist meist das die Ursache. Gegenmittel:
die betroffene Strecke aus der Wertung nehmen oder `weight` senken.

**Nach dem Umstellen von Möbeln neu kalibrieren.** Die Baseline beschreibt die
Wohnung, wie sie beim Kalibrieren aussah.
