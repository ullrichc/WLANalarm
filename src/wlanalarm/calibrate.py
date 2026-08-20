"""Kalibrierung: das Ruheverhalten der Wohnung vermessen.

Waehrend der Kalibrierung darf sich niemand in der Wohnung bewegen. Gemessen
wird, wie stark jede Funkstrecke von sich aus schwankt - daraus ergeben sich
zwei Dinge:

1. die Ruhe-Baseline, gegen die der Detektor spaeter vergleicht,
2. eine Einschaetzung, welche Strecken ueberhaupt als Sensor taugen.

Punkt 2 ist der eigentliche Gewinn: er beantwortet die Frage, welche der
Geraete im Haushalt man ueberwachen sollte - genau die Auswahl, die Comcast
seinen Nutzern per App abverlangt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .baseline import BaselineSnapshot, median, robust_scale
from .config import Config
from .detector import MotionDetector
from .selection import evaluate, selected_ids
from .sources.base import SampleSource, SourceError

log = logging.getLogger(__name__)

#: Schwellen fuer die Eignungsnote (Ruhe-Aktivitaet in dB).
GRADE_THRESHOLDS = (
    (1.2, "sehr gut", "sehr ruhige Strecke - idealer Sensor"),
    (2.5, "gut", "brauchbar ruhig"),
    (4.0, "maessig", "unruhig - erhoehte Gefahr von Fehlalarmen"),
)
GRADE_UNUSABLE = ("ungeeignet", "zu unruhig - als Sensor nicht verwendbar")

#: Ein Geraet muss in mindestens so vielen Messzyklen aufgetaucht sein.
MIN_COVERAGE = 0.8

#: Eine Strecke muss ihren Messwert oft genug erneuern, um Bewegung ueberhaupt
#: abbilden zu koennen. Die FRITZ!Box aktualisiert den Wert einer Verbindung
#: nur, wenn dort auch Daten fliessen - ein Geraet, das alle zwei Minuten ein
#: Lebenszeichen sendet, liefert dazwischen denselben Wert. Weniger als eine
#: Aenderung je zehn Messungen reicht fuer eine Erkennung im Sekundenbereich
#: nicht aus.
MIN_CHANGE_RATE = 0.1


@dataclass
class LinkReport:
    """Kalibrierergebnis einer Funkstrecke."""

    link_id: str
    label: str
    peer_mac: str
    peer_name: str
    band: str
    samples: int
    coverage: float
    #: Anteil der Messungen, bei denen sich der Signalwert geaendert hat.
    change_rate: float
    #: Zahl der unterschiedlichen Signalwerte waehrend der Messung.
    distinct_values: int
    rssi_median: float | None
    activity_median: float
    activity_scale: float
    grade: str
    note: str
    usable: bool
    is_mesh_node: bool = False

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "label": self.label,
            "peer_mac": self.peer_mac,
            "peer_name": self.peer_name,
            "band": self.band,
            "samples": self.samples,
            "coverage": round(self.coverage, 3),
            "change_rate": round(self.change_rate, 4),
            "distinct_values": self.distinct_values,
            "rssi_median": self.rssi_median,
            "activity_median": round(self.activity_median, 3),
            "activity_scale": round(self.activity_scale, 3),
            "grade": self.grade,
            "note": self.note,
            "usable": self.usable,
            "is_mesh_node": self.is_mesh_node,
        }

    def baseline(self) -> BaselineSnapshot:
        return BaselineSnapshot(
            median=self.activity_median,
            scale=self.activity_scale,
            samples=self.samples,
            updated=time.time(),
        )


@dataclass
class CalibrationResult:
    duration: float
    scans: int
    errors: int
    links: list[LinkReport] = field(default_factory=list)

    @property
    def usable(self) -> list[LinkReport]:
        return [link for link in self.links if link.usable]

    def baselines(self) -> dict[str, BaselineSnapshot]:
        return {link.link_id: link.baseline() for link in self.usable}

    def advice(self, config: Config) -> list[str]:
        """Konkrete Hinweise zur Einrichtung."""
        notes: list[str] = []
        usable = self.usable
        if not usable:
            notes.append(
                "Keine einzige brauchbare Funkstrecke gefunden. Pruefe, ob "
                "stationaere Geraete per WLAN verbunden sind, und ob die "
                "Mesh-Liste RSSI-Werte liefert (wlanalarm discover)."
            )
            return notes
        if len(usable) < config.detector.min_links:
            notes.append(
                f"Nur {len(usable)} brauchbare Strecke(n), die Erkennung verlangt "
                f"aber {config.detector.min_links} gleichzeitig ausschlagende. "
                f"Setze detector.min_links auf {len(usable)} oder schliesse "
                f"weitere stationaere Geraete ans WLAN an."
            )
        # Der Fall, der jede Feinabstimmung vergeblich macht: Die Box erneuert
        # die Messwerte zu selten, weil die Geraete kaum funken.
        traege = [
            link for link in self.links
            if link.coverage >= MIN_COVERAGE and link.change_rate < MIN_CHANGE_RATE
        ]
        if traege:
            namen = ", ".join(link.peer_name or link.peer_mac for link in traege)
            notes.append(
                f"Bei diesen Strecken erneuert die FRITZ!Box den Messwert zu selten: "
                f"{namen}. Die Box aktualisiert den Wert einer Verbindung nur, wenn "
                f"dort Daten fliessen. Geraete im Stromsparmodus - Steckdosen, "
                f"Sensoren, Luftreiniger - senden oft nur alle ein bis zwei Minuten "
                f"ein Lebenszeichen; dazwischen steht derselbe Wert. Fuer eine "
                f"Erkennung im Sekundenbereich braucht es Geraete, die dauernd "
                f"funken: ein eingeschalteter Fernseher, ein Lautsprecher mit "
                f"laufender Wiedergabe, eine Ueberwachungskamera mit Videostrom "
                f"oder ein per WLAN angebundener Repeater."
            )

        if not any(link.is_mesh_node for link in usable):
            # Bei per LAN angebundenen Repeatern gibt es keine Funkstrecke zum
            # Repeater - das ist normal und kein Mangel. Deshalb ist der Hinweis
            # bewusst als Wenn-dann formuliert und nicht als Aufforderung.
            notes.append(
                "Es ist keine Funkstrecke zu einem Repeater dabei. Falls ein "
                "Repeater vorhanden ist und per WLAN angebunden ist, waere das "
                "die stabilste Sensorstrecke ueberhaupt. Haengt er dagegen am "
                "LAN-Kabel, gibt es dorthin keine Funkstrecke - dann zaehlen "
                "allein die stationaeren Geraete an seinem WLAN."
            )
        weak = [link for link in usable if link.rssi_median is not None and link.rssi_median < -70]
        if weak:
            names = ", ".join(link.peer_name or link.peer_mac for link in weak)
            notes.append(
                f"Schwaches Signal bei: {names}. Unter -70 dBm schwankt der RSSI "
                f"ohnehin stark, das erzeugt Fehlalarme."
            )
        bands = {link.band for link in usable}
        if bands == {"2.4 GHz"}:
            notes.append(
                "Alle Sensorstrecken liegen auf 2,4 GHz. 5 und 6 GHz reagieren "
                "wegen der kuerzeren Wellenlaenge deutlich empfindlicher auf "
                "Bewegung - stationaere Geraete moeglichst dorthin umziehen."
            )
        return notes


def calibrate(
    config: Config,
    source: SampleSource,
    duration: float = 600.0,
    progress=None,
    clock=time.time,
    sleep=time.sleep,
) -> CalibrationResult:
    """Ruhephase vermessen.

    Args:
        duration: Messdauer in Sekunden. Unter fuenf Minuten wird das Ergebnis
            unzuverlaessig, weil zu wenige Fensterwerte zusammenkommen.
        progress: optionaler Rueckruf ``(vergangen, gesamt, aktive_strecken)``.
    """
    detector = MotionDetector(config)
    interval = config.sampling.interval
    start = clock()
    deadline = start + duration

    #: link_id -> (Aktivitaetswerte, RSSI-Werte, Beispiel-Sample)
    activity: dict[str, list[float]] = {}
    rssi: dict[str, list[float]] = {}
    seen: dict[str, int] = {}
    examples: dict[str, object] = {}
    scans = 0
    errors = 0

    while clock() < deadline:
        loop_start = clock()
        try:
            scan = source.scan()
        except SourceError as exc:
            errors += 1
            log.warning("Messung waehrend der Kalibrierung fehlgeschlagen: %s", exc)
            sleep(interval)
            continue

        scans += 1
        chosen = selected_ids(evaluate(scan, config))
        # Der Detektor rechnet hier nur die Fenstermerkmale aus; sein Urteil
        # interessiert waehrend der Kalibrierung nicht.
        result = detector.update(scan, chosen)
        for link in result.links:
            seen[link.link_id] = seen.get(link.link_id, 0) + 1
            if link.activity_db is not None:
                activity.setdefault(link.link_id, []).append(link.activity_db)
            if link.rssi_dbm is not None:
                rssi.setdefault(link.link_id, []).append(link.rssi_dbm)
        for sample in scan.samples:
            examples[sample.link_id] = sample

        if progress is not None:
            progress(clock() - start, duration, len(chosen))

        remaining = interval - (clock() - loop_start)
        if remaining > 0:
            sleep(remaining)

    reports = _build_reports(activity, rssi, seen, examples, scans)
    return CalibrationResult(
        duration=clock() - start,
        scans=scans,
        errors=errors,
        links=sorted(reports, key=lambda r: (not r.usable, r.activity_median)),
    )


def _build_reports(
    activity: dict[str, list[float]],
    rssi: dict[str, list[float]],
    seen: dict[str, int],
    examples: dict,
    scans: int,
) -> list[LinkReport]:
    reports: list[LinkReport] = []
    for link_id, values in activity.items():
        sample = examples.get(link_id)
        coverage = seen.get(link_id, 0) / scans if scans else 0.0
        centre = median(values)
        scale = robust_scale(values, centre)
        signale = rssi.get(link_id, [])
        wechsel = sum(
            1 for i in range(1, len(signale)) if signale[i] != signale[i - 1]
        )
        change_rate = wechsel / (len(signale) - 1) if len(signale) > 1 else 0.0
        grade, note, usable = _beurteilen(
            centre, coverage, len(values), change_rate, len(set(signale))
        )

        reports.append(
            LinkReport(
                link_id=link_id,
                label=getattr(sample, "label", link_id),
                peer_mac=getattr(sample, "peer_mac", ""),
                peer_name=getattr(sample, "peer_name", ""),
                band=getattr(sample, "band", "unbekannt"),
                samples=len(values),
                coverage=coverage,
                change_rate=change_rate,
                distinct_values=len(set(signale)),
                rssi_median=round(median(rssi[link_id]), 1) if rssi.get(link_id) else None,
                activity_median=centre,
                activity_scale=scale,
                grade=grade,
                note=note,
                usable=usable,
                is_mesh_node=bool(getattr(sample, "peer_is_mesh_node", False)),
            )
        )
    return reports


def _beurteilen(
    activity_median: float,
    coverage: float,
    messpunkte: int,
    change_rate: float,
    distinct_values: int,
) -> tuple[str, str, bool]:
    """Note, Begruendung und Eignung einer Strecke.

    Die Reihenfolge ist wesentlich: Ein Ausschlussgrund muss die Note bestimmen
    und nicht bloss eine Randbemerkung sein. In einer frueheren Fassung stand
    hinter einer Strecke "sehr gut", obwohl sie nur in 13 % der Messungen
    sichtbar war - Note und Begruendung widersprachen sich.
    """
    if messpunkte < 20:
        return "unklar", "zu wenige Messpunkte fuer ein Urteil", False

    if coverage < MIN_COVERAGE:
        return (
            "ungeeignet",
            f"nur in {coverage:.0%} der Messungen sichtbar - das Geraet "
            f"schaltet sich zwischendurch ab",
            False,
        )

    # Der wichtigste Fall, und der am leichtesten zu uebersehende: Ein Wert, der
    # sich nie aendert, sieht wie ein vollkommen ruhiger Sensor aus. In
    # Wahrheit traegt er keinerlei Information - was sich in Ruhe nicht regt,
    # regt sich auch bei Bewegung nicht.
    if distinct_values <= 1:
        return (
            "ungeeignet",
            "der Messwert hat sich kein einziges Mal geaendert - diese Strecke "
            "liefert keine Information",
            False,
        )
    if change_rate < MIN_CHANGE_RATE:
        return (
            "ungeeignet",
            f"der Messwert erneuert sich nur in {change_rate:.0%} der Messungen "
            f"- zu selten, um Bewegung abzubilden",
            False,
        )

    for limit, grade, note in GRADE_THRESHOLDS:
        if activity_median < limit:
            return grade, note, True
    return GRADE_UNUSABLE[0], GRADE_UNUSABLE[1], False
