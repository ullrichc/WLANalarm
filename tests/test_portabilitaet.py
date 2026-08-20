"""Prüfungen, die verhindern, dass das Projekt unter Windows auseinanderfällt.

Die Entwicklung läuft unter Linux; diese Tests fangen die drei Stolperstellen
ab, die dort nicht auffallen, unter Windows aber sofort.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
QUELLEN = sorted((WURZEL / "src" / "wlanalarm").rglob("*.py"))


def test_das_repository_enthaelt_keine_symlinks():
    """Git legt unter Windows ohne Entwicklermodus statt einer Verknüpfung
    eine Textdatei mit dem Pfad an - die Datei ist dann unbrauchbar."""
    ausgabe = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=WURZEL, capture_output=True, text=True, check=True,
    ).stdout
    symlinks = [
        zeile.split("\t", 1)[1]
        for zeile in ausgabe.splitlines()
        if zeile.startswith("120000")
    ]
    assert symlinks == []


#: Module, deren Zeichenketten auf der Konsole landen können.
KONSOLENMODULE = [
    "cli.py", "selection.py", "calibrate.py", "alarm.py",
    "detector.py", "engine.py", "diagnose.py",
]


@pytest.mark.parametrize("codepage", ["cp850", "cp1252"])
@pytest.mark.parametrize("modul", KONSOLENMODULE)
def test_konsolenausgaben_sind_auf_alten_codepages_darstellbar(codepage, modul):
    """Die klassische Windows-Konsole läuft je nach Systemeinstellung noch auf
    einer 8-Bit-Codepage. Umlaute gehen dort, typografische Zeichen wie der
    Halbgeviertstrich nicht - und ein UnicodeEncodeError bricht die Ausgabe ab.

    Geprüft wird jedes Modul, dessen Texte auf der Konsole erscheinen können,
    nicht nur cli.py: Die Begründungen der Geräteauswahl etwa entstehen in
    selection.py und werden von 'wlanalarm discover' ausgegeben.
    """
    baum = ast.parse((WURZEL / "src" / "wlanalarm" / modul).read_text(encoding="utf-8"))
    unzulaessig = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            try:
                knoten.value.encode(codepage)
            except UnicodeEncodeError as fehler:
                zeichen = fehler.object[fehler.start : fehler.end]
                unzulaessig.append(f"Zeile {knoten.lineno}: {zeichen!r}")
    assert unzulaessig == [], (
        f"Nicht in {codepage} darstellbare Zeichen in {modul}: {unzulaessig}"
    )


def test_dateizugriffe_geben_die_kodierung_an():
    """Ohne explizites encoding nimmt Python unter Windows die Codepage des
    Systems statt UTF-8 - Konfigurationsdateien mit Umlauten scheitern dann."""
    ohne_kodierung = []
    for quelle in QUELLEN:
        baum = ast.parse(quelle.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            name = _aufrufname(knoten.func)
            if name not in ("open", "read_text", "write_text"):
                continue
            # Binärmodus braucht keine Kodierung.
            modus = next(
                (a.value for a in knoten.args if isinstance(a, ast.Constant)
                 and isinstance(a.value, str) and set(a.value) <= set("rwaxbt+")),
                "",
            )
            if "b" in modus:
                continue
            if not any(k.arg == "encoding" for k in knoten.keywords):
                ohne_kodierung.append(f"{quelle.name}:{knoten.lineno} {name}()")
    assert ohne_kodierung == []


def test_pfade_werden_ueber_pathlib_gebaut():
    """Pfade nie per String-Verkettung oder os.path zusammensetzen - pathlib
    kennt den plattformrichtigen Trenner, ein fester Schrägstrich nicht.

    Geprüft wird auf os.path-Aufrufe statt auf Backslashes im Text: reguläre
    Ausdrücke und ASCII-Diagramme enthalten legitim welche, und ein Textscan
    liefert dort nur Fehlalarme.
    """
    verdaechtig = []
    for quelle in QUELLEN:
        baum = ast.parse(quelle.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Attribute):
                continue
            # os.path.join(...), os.sep, os.path.dirname(...) und Verwandte
            if _aufrufname(getattr(knoten, "value", None)) in ("path",) or knoten.attr == "sep":
                if _wurzel(knoten) == "os":
                    verdaechtig.append(f"{quelle.name}:{knoten.lineno} os.{knoten.attr}")
    assert verdaechtig == []


def _wurzel(knoten) -> str:
    """Den äußersten Namen einer Attributkette liefern: os.path.join -> 'os'."""
    while isinstance(knoten, ast.Attribute):
        knoten = knoten.value
    return knoten.id if isinstance(knoten, ast.Name) else ""


def _aufrufname(knoten) -> str:
    if isinstance(knoten, ast.Name):
        return knoten.id
    if isinstance(knoten, ast.Attribute):
        return knoten.attr
    return ""
