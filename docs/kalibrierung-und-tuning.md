# Kalibrierung und Feinabstimmung

## Wie der Detektor urteilt

Vier Schritte, je Funkstrecke:

**1. Aktivitätskennzahl.** Aus den letzten `window_seconds` (12 s) wird eine Zahl
in dB gebildet:

```
Aktivität = 1.0  · Streuung(RSSI)
          + 1.0  · mittlere Änderung zwischen Messpunkten
          + 0.25 · Spanne im Fenster
          + 0.5  · 10 · relative Streuung der Datenrate
          + 0.8  · (Streuung + mittlere Änderung des Störabstands) / 2
```

Der letzte Term entfällt, wenn die FRITZ!Box keinen Störabstand liefert. Ab
Mesh-Schema 8.x (FRITZ!OS 8.2x) tut sie es, und der Wert reagiert dort oft
empfindlicher auf Bewegung als die Feldstärke allein.

Die *mittlere Änderung* trennt schnelle Schwankungen von langsamem Driften.
Beides kann dieselbe Standardabweichung ergeben, aber nur schnelle Schwankungen
deuten auf Bewegung hin; Driften geht auf Temperatur oder einen Kanalwechsel
zurück.

**2. Vergleich mit der Ruhe.** Die Kennzahl wird gegen Median und MAD *derselben*
Strecke normiert:

```
z = (Aktivität − Ruhe-Median) / max(Ruhe-Streuung, 0.35 dB)
```

Median und MAD statt Mittelwert und Standardabweichung, weil die FRITZ!Box
gelegentlich einen völlig danebenliegenden RSSI liefert. Ein einziger solcher
Ausreißer würde eine Standardabweichung so aufblähen, dass danach minutenlang
nichts mehr erkannt wird.

Jede Strecke bekommt ihre eigene Schwelle. Eine Strecke zum Repeater im
Nebenzimmer rauscht anders als die zum Lautsprecher zwei Meter neben der Box –
eine gemeinsame absolute Schwelle könnte für keine von beiden stimmen.

**3. Score.** `score = z / 8`, gedeckelt auf 0…1. Zusätzlich muss die Aktivität
die Ruhe um mindestens `min_delta_db` (0,5 dB) übersteigen – sonst käme eine
extrem stabile Strecke schon bei Zehntel-dB in die Sättigung.

**4. Gesamturteil.** Bewegung gilt als erkannt, wenn **mindestens `min_links`
Strecken gleichzeitig** über `trigger_score` liegen, und das über
`trigger_consecutive` Messzyklen in Folge.

## Der wichtigste Punkt: min_links

Das ist der wirksamste Schutz vor Fehlalarmen, und zwar mit Abstand.

Ein einzelnes Gerät erzeugt regelmäßig Signale, die von Bewegung nicht zu unterscheiden sind: Firmware-Update, Stromsparmodus,
Kanalwechsel, ein voller Sendepuffer. All das sieht in *einer* Funkstrecke
genauso aus wie ein Mensch. Ein Mensch stört aber **mehrere** Strecken
gleichzeitig, ein auffälliges Gerät nur seine eigene.

```yaml
detector:
  min_links: 2      # Voreinstellung
```

Nur wenn insgesamt weniger als `min_links` Strecken zur Verfügung stehen, darf
eine einzelne sehr starke Strecke (`strong_score`) allein auslösen – sonst wäre
die Bedingung in einer kleinen Wohnung nie erfüllbar. Wer diese Abkürzung
generell will, setzt `allow_single_strong: true` – und handelt sich damit
Fehlalarme ein.

## Kalibrieren

```bash
wlanalarm calibrate --minutes 15
```

Während der Messung darf sich niemand in der Wohnung bewegen. Fünfzehn Minuten
sind ein guter Wert; unter fünf wird es unzuverlässig.

Die Noten:

| Ruhe-Aktivität | Note | Bedeutung |
|---|---|---|
| < 1,2 dB | sehr gut | idealer Sensor |
| < 2,5 dB | gut | brauchbar |
| < 4,0 dB | mäßig | erhöhte Fehlalarmgefahr |
| ≥ 4,0 dB | ungeeignet | wird nicht bewertet |

Zwei Spalten entscheiden vor jeder Note:

* **Sicht** – wie oft das Gerät überhaupt in der Liste stand. Wer nur in 60 %
  der Messungen auftaucht, schaltet sich zwischendurch ab.
* **Neu** – wie oft die FRITZ!Box einen *neuen* Messwert lieferte. Das ist die
  wichtigere der beiden. Die Box erneuert den Wert nur, wenn Daten fließen;
  ein sparsames Gerät liefert minutenlang denselben. Unter 10 % ist eine
  Strecke unbrauchbar, auch wenn sie mit `Ruhe 0.00 dB` wie der ideale Sensor
  aussieht. Ein Wert, der sich nie ändert, kann Bewegung nicht anzeigen.

Die Baselines landen in `state/wlanalarm.sqlite3` und werden beim Start wieder
geladen. Im Betrieb schreibt WLANalarm sie fort – aber **nur in Ruhephasen**.
Bei erkannter Bewegung wird nicht gelernt, sonst würde bei längerer Anwesenheit
die Bewegung selbst zum Normalzustand und die Anlage verstummte.

Nach dem Umstellen von Möbeln oder dem Versetzen von Geräten neu kalibrieren.
Baselines, die älter als 30 Tage sind, werden verworfen.

## Fehlalarme abstellen

### Ursache finden

```yaml
storage:
  record_samples: true
```

Eine Nacht mitschneiden lassen, dann:

```bash
wlanalarm replay state/recordings/samples-2026-08-19.ndjson
```

```
1800 Messzyklen über 1 h 0 min
Höchster Score: 0.91
Bewegungsepisoden: 3
  19.08. 02:14:06  Dauer 38 s
  19.08. 03:47:22  Dauer 24 s
  19.08. 04:02:10  Dauer 51 s

Entspricht 3.0 Episoden pro Stunde.
```

Mit `-v` zeigt `replay`, **welche** Strecken jeweils ausgeschlagen haben. Ist es
immer dieselbe, ist die Sache klar. Nach jeder Änderung an der Konfiguration
lässt sich dieselbe Aufzeichnung erneut durchrechnen – Sekunden statt Nächte.

### Stellschrauben, in dieser Reihenfolge

**1. `min_links` erhöhen.** Von 2 auf 3, wenn genug Strecken da sind. Der mit
Abstand größte Hebel, ohne die Empfindlichkeit für echte Bewegung nennenswert zu
senken.

**2. Die Störquelle ausschließen.**

```yaml
links:
  - mac: "AA:BB:CC:DD:EE:07"
    name: "Kamera Eingang"
    ignore: true          # ganz raus
  # oder milder:
  - mac: "AA:BB:CC:DD:EE:08"
    weight: 0.5           # zählt nur halb
```

**3. `trigger_score` anheben.** 0,55 → 0,65. Wirkt global, kostet
Empfindlichkeit. Erst hierher greifen, wenn 1 und 2 nicht reichen.

**4. `trigger_consecutive` erhöhen.** Von 2 auf 3 Messzyklen. Kostet etwa zwei
Sekunden Reaktionszeit und filtert einzelne Störspitzen zuverlässig weg.

**5. Fenster verlängern.** `sampling.window_seconds` von 12 auf 20. Glättet,
macht die Erkennung aber träger und für kurze Durchgänge unempfindlicher.

### Umgekehrt: zu unempfindlich

Wenn ein Gang durch die Wohnung nicht auffällt:

* `wlanalarm monitor` laufen lassen und dabei durch die Wohnung gehen – zeigt
  live, welche Strecke wie stark reagiert. Bleibt alles unter 0,3, stimmt die
  **Geometrie** nicht: Der Laufweg kreuzt keine Funkstrecke. Dagegen hilft kein
  Schwellenwert, sondern nur ein Gerät an einer anderen Stelle.
* `trigger_score` auf 0,45 senken.
* `min_links` auf 1 – nur wenn Fehlalarme hinnehmbar sind.
* Ein weiteres stationäres Gerät ins 5- oder 6-GHz-Netz bringen.
* Geräte vom 2,4-GHz- ins 5-GHz-Netz umziehen.

## Reaktionszeit

Mit den Voreinstellungen vergehen zwischen dem Betreten und der Erkennung etwa
**4 bis 8 Sekunden**:

| Beitrag | Zeit |
|---|---|
| Messtakt | 0–2 s |
| Fenster muss sich füllen | ~2–4 s |
| `trigger_consecutive: 2` | 2–4 s |

Dazu kommt die Eingangsverzögerung (`alarm.entry_delay`, 30 s), bevor der Alarm
tatsächlich hinausgeht. Schneller geht es kaum – die FRITZ!Box aktualisiert die
Mesh-Liste nur alle ein bis zwei Sekunden, `sampling.interval` darunter zu
setzen bringt nichts außer Last.

Umgekehrt dauert es nach dem Ende der Bewegung etwa 35 Sekunden bis zur
Ruhemeldung (`clear_seconds` 25 s plus Fensterablauf). Das ist Absicht: Es
verhindert, dass eine kurze Pause beim Gehen die Erkennung zurücksetzt.

## Alle Parameter

| Parameter | Vorgabe | Wirkung |
|---|---|---|
| `sampling.interval` | 2.0 | Messtakt. Kleiner bringt nichts. |
| `sampling.window_seconds` | 12.0 | Analysefenster. Größer = träger, ruhiger. |
| `sampling.baseline_seconds` | 900.0 | Gedächtnis für den Ruhezustand. |
| `detector.trigger_score` | 0.55 | Auslöseschwelle je Strecke. |
| `detector.clear_score` | 0.30 | Rückfallschwelle (Hysterese). |
| `detector.min_links` | 2 | **wichtigster Schutz vor Fehlalarmen** |
| `detector.trigger_consecutive` | 2 | Messzyklen in Folge. |
| `detector.clear_seconds` | 25.0 | Nachlaufzeit bis „Ruhe". |
| `detector.z_full_scale` | 8.0 | z-Wert für Score 1,0. |
| `detector.min_delta_db` | 0.5 | Mindestabstand zur Ruhe. |
| `detector.min_baseline_scale_db` | 0.35 | Untergrenze der Ruhestreuung. |
| `detector.max_baseline_activity_db` | 6.0 | darüber gilt eine Strecke als untauglich. |
| `detector.allow_single_strong` | false | Einzelausschlag genügt. Fehlalarmquelle. |
| `detector.strong_score` | 0.85 | Ab hier gilt eine Strecke als deutlich ausgeschlagen. |
| `detector.stale_after_seconds` | 120.0 | Danach wird eine verschwundene Strecke vergessen. |
| `detector.weight_std` | 1.0 | Gewicht der RSSI-Streuung. |
| `detector.weight_jitter` | 1.0 | Gewicht der Änderung zwischen Messpunkten. |
| `detector.weight_range` | 0.25 | Gewicht der Spanne im Fenster. |
| `detector.weight_rate` | 0.5 | Gewicht der Datenratenschwankung. |
| `detector.weight_snr` | 0.8 | Gewicht des Störabstands (ab Mesh-Schema 8.x). |
| `sampling.warmup_samples` | 20 | Messungen, bevor überhaupt bewertet wird. |
