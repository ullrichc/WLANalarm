"""Kommandozeile von WLANalarm."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .alarm import ARMED_STATES, STATE_DISARMED, AlarmEvent
from .calibrate import calibrate
from .config import Config, ConfigError, load_config
from .detector import MotionDetector
from .diagnose import bericht as diagnose_bericht
from .engine import Engine
from .notify import build_hub, build_notifier
from .presence import PresenceTracker
from .recorder import Recorder
from .selection import evaluate, selected_ids
from .sources.base import SourceError
from .sources.factory import build_source, describe_source
from .sources.replay import iter_recording
from .storage import Storage
from .web import WebServer

log = logging.getLogger("wlanalarm")

DEFAULT_CONFIG = "config.yaml"


# --------------------------------------------------------------------------- #
# Gemeinsame Hilfen
# --------------------------------------------------------------------------- #


def configure_console() -> None:
    """Ausgabe auf UTF-8 umstellen, soweit die Konsole das zulässt.

    Die klassische Windows-Konsole läuft je nach Systemeinstellung noch auf
    cp850 oder cp1252. Umlaute gehen dort zwar, typografische Zeichen aber
    nicht - ohne diese Umstellung bricht die Ausgabe mit einem
    UnicodeEncodeError ab. ``errors="replace"`` sorgt dafür, dass im
    schlimmsten Fall ein Ersatzzeichen erscheint statt eines Abbruchs.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # umgeleitete Ausgabe oder Testumgebung
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - je nach Konsole
            pass


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Die HTTP-Bibliothek unter fritzconnection ist im Debugmodus sehr gespraechig.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # fritzconnection protokolliert Verbindungsfehler selbst und ausfuehrlich;
    # WLANalarm meldet dieselbe Ursache verstaendlicher. Nur im Debugmodus
    # ist der Originaltext interessant.
    if level.upper() != "DEBUG":
        logging.getLogger("fritzconnection").setLevel(logging.CRITICAL)


def read_config(args) -> Config:
    config = load_config(args.config)
    if getattr(args, "verbose", False):
        config.log_level = "DEBUG"
    setup_logging(config.log_level)
    return config


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {(seconds % 3600) // 60} min"


# --------------------------------------------------------------------------- #
# Befehle
# --------------------------------------------------------------------------- #


def cmd_check(args) -> int:
    """Konfiguration und Erreichbarkeit der FRITZ!Box prüfen."""
    config = read_config(args)
    print(f"Konfiguration {args.config}: in Ordnung")
    print(f"  Takt        : {config.sampling.interval} s")
    print(f"  Fenster     : {config.sampling.window_seconds} s")
    print(f"  Baseline    : {_fmt_duration(config.sampling.baseline_seconds)}")
    print(f"  Kanäle      : {len(config.notifiers) or 'nur Log'}")
    baender = ", ".join(config.selection.bands) if config.selection.bands else "alle"
    print(f"  Bänder      : {baender}")
    print(f"  min_links   : {config.detector.min_links}")

    source = build_source(config.fritzbox)
    try:
        print(f"  FRITZ!Box   : {describe_source(source)}")
        scan = source.scan()
        candidates = evaluate(scan, config)
        chosen = [c for c in candidates if c.selected]
        print(f"  Funkstrecken: {len(scan.samples)} gefunden, {len(chosen)} nutzbar")
        if len(chosen) < config.detector.min_links:
            print(
                f"  ACHTUNG     : detector.min_links = {config.detector.min_links}, "
                f"aber nur {len(chosen)} Strecke(n) nutzbar - so wird nie "
                f"ein Alarm ausgelöst."
            )
            return 1
    except SourceError as exc:
        print(f"  FEHLER      : {exc}", file=sys.stderr)
        return 2
    finally:
        source.close()
    return 0


def cmd_discover(args) -> int:
    """Alle Funkstrecken auflisten und die Auswahl begründen."""
    config = read_config(args)
    source = build_source(config.fritzbox)
    try:
        scan = source.scan()
    except SourceError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    finally:
        source.close()

    candidates = evaluate(scan, config)
    if args.json:
        print(json.dumps([c.to_dict() for c in candidates], ensure_ascii=False, indent=2))
        return 0

    if not candidates:
        print("Keine WLAN-Funkstrecken gefunden.")
        return 1

    if args.yaml:
        print(_links_yaml(candidates))
        return 0

    print(f"{len(candidates)} Funkstrecken an {describe_source_name(scan)}:\n")
    header = (f"{'':3s} {'Gerät':22s} {'MAC':18s} {'Band':8s} {'RSSI':>7s} "
              f"{'Zone':10s} Begründung")
    print(header)
    print("-" * len(header))
    for candidate in sorted(candidates, key=lambda c: (not c.selected, c.sample.peer_name)):
        sample = candidate.sample
        rssi = f"{sample.rssi_dbm:.0f} dBm" if sample.rssi_dbm is not None else "     -"
        mark = " ok" if candidate.selected else "  ."
        name = (sample.peer_name or sample.peer_mac)[:22]
        print(f"{mark:3s} {name:22s} {sample.peer_mac:18s} {sample.band:8s} "
              f"{rssi:>7s} {candidate.zone:10s} {candidate.reason}")
    chosen = sum(1 for c in candidates if c.selected)
    print(f"\n{chosen} Strecke(n) werden als Sensor verwendet.")
    if chosen < config.detector.min_links:
        print(f"Das reicht nicht: die Erkennung verlangt {config.detector.min_links} "
              f"gleichzeitig ausschlagende Strecken.")
        print("Mit 'wlanalarm discover --yaml' erzeugen Sie einen fertigen "
              "links-Abschnitt für config.yaml, in dem Sie die stationären "
              "Geräte auswählen.")
    else:
        print("Als Nächstes: wlanalarm calibrate --minutes 10 "
              "(dabei die Wohnung nicht betreten)")
    return 0


def _links_yaml(candidates) -> str:
    """Fertigen ``links``-Abschnitt für die Konfiguration erzeugen.

    Schliesst die Lücke zwischen "discover zeigt meine Geräte" und "wie
    schreibe ich das in die Konfiguration": Jede gefundene Gegenstelle steht
    hier mit ihrer MAC-Adresse, auskommentiert und nach Eignung sortiert.
    """
    zeilen = [
        "# Von 'wlanalarm discover --yaml' erzeugt.",
        "# Kommentarzeichen entfernen bei allen Geräten, die dauerhaft am selben",
        "# Platz stehen und eingeschaltet bleiben - Fernseher, Lautsprecher,",
        "# Drucker, Kameras, Luftreiniger, Steckdosen.",
        "# Handys, Tablets und Notebooks bleiben auskommentiert.",
        "#",
        "# Ausdrücklich aufgeführte Geräte umgehen den Bandfilter, können",
        "# also auch auf 2,4 GHz liegen.",
        "links:",
    ]
    gesehen: set[str] = set()
    for candidate in sorted(
        candidates, key=lambda c: (not c.selected, c.sample.peer_name or c.sample.peer_mac)
    ):
        sample = candidate.sample
        if sample.peer_mac in gesehen:
            continue
        gesehen.add(sample.peer_mac)
        rssi = f"{sample.rssi_dbm:.0f} dBm" if sample.rssi_dbm is not None else "ohne Messwert"
        name = sample.peer_name or sample.peer_mac
        zeilen.append(f"  # {name} - {sample.band}, {rssi}")
        zeilen.append(f"  # - mac: \"{sample.peer_mac}\"")
        zeilen.append(f"  #   name: \"{name}\"")
        zeilen.append('  #   zone: "default"')
    return "\n".join(zeilen)


def describe_source_name(scan) -> str:
    aps = {sample.ap_name for sample in scan.samples if sample.ap_name}
    return ", ".join(sorted(aps)) or "der FRITZ!Box"


def cmd_diagnose(args) -> int:
    """Zeigen, was die FRITZ!Box in der Mesh-Liste tatsächlich liefert.

    Gedacht für den Fall, dass Funkstrecken fehlen oder keine Messwerte
    ankommen: Der Bericht macht sichtbar, welche Felder die eigene
    FRITZ!OS-Version verwendet und an welcher Stelle eine Verbindung
    verloren geht.
    """
    config = read_config(args)
    source = build_source(config.fritzbox)
    holen = getattr(source, "fetch_mesh_list", None)
    if holen is None:
        print("Diese Quelle hat keine Mesh-Liste. In der Konfiguration "
              "fritzbox.source auf 'mesh' oder 'auto' stellen.", file=sys.stderr)
        return 2
    try:
        mesh = holen()
    except SourceError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    finally:
        source.close()

    if args.raw:
        # Ungefiltert - enthaelt MAC-Adressen und Geraetenamen. Nur zur
        # eigenen Ansicht, nicht zum Weitergeben gedacht.
        print(json.dumps(mesh, ensure_ascii=False, indent=2))
        return 0

    zeilen = diagnose_bericht(mesh)
    ausgabe = "\n".join(zeilen)
    print(ausgabe)
    if args.output:
        Path(args.output).write_text(ausgabe + "\n", encoding="utf-8")
        print(f"\nBericht gespeichert: {args.output}")
    else:
        print("\nDieser Bericht enthält keine Gerätenamen und keine "
              "MAC-Adressen und kann weitergegeben werden.")
    return 0


def cmd_calibrate(args) -> int:
    """Ruhephase vermessen und Baselines speichern."""
    config = read_config(args)
    duration = args.minutes * 60
    source = build_source(config.fritzbox)

    print(f"Kalibrierung läuft {_fmt_duration(duration)}.")
    print("WICHTIG: In dieser Zeit darf sich niemand in der Wohnung bewegen.\n")

    last_line = [0.0]

    def progress(elapsed: float, total: float, links: int) -> None:
        if elapsed - last_line[0] < 5 and elapsed < total - 1:
            return
        last_line[0] = elapsed
        share = elapsed / total if total else 1.0
        filled = int(share * 30)
        bar = "#" * filled + "." * (30 - filled)
        sys.stdout.write(
            f"\r  [{bar}] {share:4.0%}  noch {_fmt_duration(max(0, total - elapsed))}, "
            f"{links} Strecken "
        )
        sys.stdout.flush()

    try:
        result = calibrate(config, source, duration=duration, progress=progress)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    finally:
        source.close()
    print("\n")

    if not result.links:
        print("Keine auswertbaren Funkstrecken. Vorher 'wlanalarm discover' prüfen.")
        return 1

    header = (
        f"{'Gerät':24s} {'Band':8s} {'RSSI':>7s} {'Ruhe':>7s} {'Streu':>6s} "
        f"{'Sicht':>6s} {'Neu':>5s}  Bewertung"
    )
    print(header)
    print("-" * len(header))
    for report in result.links:
        rssi = f"{report.rssi_median:.0f} dBm" if report.rssi_median is not None else "     -"
        name = (report.peer_name or report.peer_mac)[:24]
        print(
            f"{name:24s} {report.band:8s} {rssi:>7s} "
            f"{report.activity_median:6.2f}dB {report.activity_scale:5.2f} "
            f"{report.coverage:5.0%} {report.change_rate:4.0%}  "
            f"{report.grade} - {report.note}"
        )
    print()
    print("  Sicht = wie oft das Gerät überhaupt in der Liste stand")
    print("  Neu   = wie oft die FRITZ!Box einen neuen Messwert lieferte")

    print(f"\n{result.scans} Messungen in {_fmt_duration(result.duration)}, "
          f"{len(result.usable)} brauchbare Strecke(n).")
    for note in result.advice(config):
        print(f"\nHinweis: {note}")

    if args.dry_run:
        print("\n--dry-run: nichts gespeichert.")
        return 0

    storage = Storage(config.storage.path)
    try:
        baselines = result.baselines()
        storage.save_baselines(baselines)
        print(f"\n{len(baselines)} Baselines gespeichert in {storage.path}")
    finally:
        storage.close()
    return 0 if result.usable else 1


def cmd_monitor(args) -> int:
    """Live-Anzeige im Terminal, ohne Alarm und ohne Benachrichtigungen."""
    config = read_config(args)
    source = build_source(config.fritzbox)
    detector = MotionDetector(config)

    storage = Storage(config.storage.path)
    seeded = detector.seed_baselines(storage.load_baselines())
    storage.close()
    if seeded:
        print(f"{seeded} kalibrierte Baselines geladen.")
    else:
        print("Keine Kalibrierung gefunden - die Baseline bildet sich erst im Betrieb.")
    print("Abbruch mit Strg-C.\n")

    try:
        while True:
            start = time.time()
            try:
                scan = source.scan()
            except SourceError as exc:
                print(f"\rMessfehler: {exc}", end="", flush=True)
                time.sleep(config.sampling.interval)
                continue
            result = detector.update(scan, selected_ids(evaluate(scan, config)))
            stamp = time.strftime("%H:%M:%S")
            state = "BEWEGUNG" if result.motion else ("bereit  " if result.ready else "aufwärm ")
            top = " | ".join(
                f"{(link.peer_name or link.peer_mac)[:14]}:{link.score:4.2f}"
                for link in result.links[:4]
            )
            print(f"\r{stamp}  {state}  Score {result.score:4.2f}  {top}   ", end="", flush=True)
            if result.motion and args.verbose:
                print()
            time.sleep(max(0.0, config.sampling.interval - (time.time() - start)))
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0
    finally:
        source.close()


def cmd_record(args) -> int:
    """Rohmesswerte für späteres Nachjustieren mitschneiden."""
    config = read_config(args)
    source = build_source(config.fritzbox)
    directory = Path(args.output or config.storage.path / "recordings")
    duration = args.minutes * 60 if args.minutes else None
    deadline = time.time() + duration if duration else None

    print(f"Aufzeichnung nach {directory}")
    print("Abbruch mit Strg-C.\n")
    count = 0
    try:
        with Recorder(directory, retention_days=config.storage.sample_retention_days,
                      compress=args.compress) as recorder:
            while deadline is None or time.time() < deadline:
                start = time.time()
                try:
                    scan = source.scan()
                except SourceError as exc:
                    log.warning("Messfehler: %s", exc)
                    time.sleep(config.sampling.interval)
                    continue
                recorder.write(scan)
                count += 1
                sys.stdout.write(f"\r  {count} Messzyklen, {len(scan.samples)} Strecken ")
                sys.stdout.flush()
                time.sleep(max(0.0, config.sampling.interval - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        source.close()
    print(f"\n{count} Messzyklen aufgezeichnet.")
    return 0


def cmd_replay(args) -> int:
    """Aufzeichnung mit der aktuellen Konfiguration durchrechnen."""
    config = read_config(args)
    detector = MotionDetector(config)

    if args.use_baselines:
        storage = Storage(config.storage.path)
        detector.seed_baselines(storage.load_baselines())
        storage.close()

    episodes: list[tuple[float, float, float]] = []
    start_ts = None
    motion_since = None
    scans = 0
    peak = 0.0

    for scan in iter_recording(args.file):
        scans += 1
        if start_ts is None:
            start_ts = scan.ts
        result = detector.update(scan, selected_ids(evaluate(scan, config)))
        peak = max(peak, result.score)
        if result.motion and motion_since is None:
            motion_since = scan.ts
        elif not result.motion and motion_since is not None:
            episodes.append((motion_since, scan.ts, scan.ts - motion_since))
            motion_since = None
        if args.verbose and result.motion:
            print(f"  {time.strftime('%H:%M:%S', time.localtime(scan.ts))} "
                  f"Score {result.score:.2f} {result.summary}")
    if motion_since is not None and start_ts is not None:
        episodes.append((motion_since, scan.ts, scan.ts - motion_since))

    if not scans:
        print("Die Aufzeichnung ist leer.")
        return 1

    span = (scan.ts - start_ts) if start_ts else 0
    print(f"\n{scans} Messzyklen über {_fmt_duration(span)}")
    print(f"Höchster Score: {peak:.2f}")
    print(f"Bewegungsepisoden: {len(episodes)}")
    for begin, _, length in episodes:
        print(f"  {time.strftime('%d.%m. %H:%M:%S', time.localtime(begin))}  "
              f"Dauer {_fmt_duration(length)}")
    if span > 0:
        per_hour = len(episodes) / (span / 3600)
        print(f"\nEntspricht {per_hour:.1f} Episoden pro Stunde.")
        if per_hour > 2:
            print("Das ist für eine leere Wohnung zu viel. Gegenmittel, in dieser "
                  "Reihenfolge: detector.min_links erhöhen, unruhige Strecken per "
                  "links[].ignore ausschließen, detector.trigger_score anheben.")
    return 0


def cmd_test_notify(args) -> int:
    """Testbenachrichtigung über alle Kanäle schicken."""
    config = read_config(args)
    if not config.notifiers:
        print("Keine Kanäle konfiguriert - es wird nur ins Log geschrieben.")
    events = {
        "motion": AlarmEvent(
            ts=time.time(), type="motion", level="motion",
            message="Testmeldung: Bewegung erkannt", mode="armed_home", zones=["test"],
        ),
        "alarm": AlarmEvent(
            ts=time.time(), type="alarm", level="alarm",
            message="Testmeldung: ALARM (nur ein Test)", mode="armed_away", zones=["test"],
        ),
    }
    event = events[args.level]

    failures = 0
    for entry in config.notifiers or []:
        if not entry.enabled:
            print(f"  {entry.name or entry.type}: deaktiviert, übersprungen")
            continue
        try:
            notifier = build_notifier(entry)
            notifier.send(event)
            notifier.close()
            print(f"  {entry.name or entry.type}: zugestellt")
        except Exception as exc:
            failures += 1
            print(f"  {entry.name or entry.type}: FEHLER - {exc}")
    if not config.notifiers:
        hub = build_hub(config)
        hub.dispatch(event)
        hub.close()
    return 1 if failures else 0


def cmd_run(args) -> int:
    """Dauerbetrieb."""
    config = read_config(args)
    source = build_source(config.fritzbox)
    storage = Storage(config.storage.path)
    hub = build_hub(config)

    recorder = None
    if config.storage.record_samples:
        recorder = Recorder(
            config.storage.path / "recordings",
            retention_days=config.storage.sample_retention_days,
        )

    presence = None
    if config.alarm.presence.enabled:
        mesh_like = source

        def connection_factory():
            connect = getattr(mesh_like, "_connect", None)
            if connect is None:
                raise SourceError("Quelle unterstützt keine Anwesenheitsabfrage")
            return connect()

        presence = PresenceTracker(config.alarm.presence, connection_factory)

    engine = Engine(config, source, storage=storage, hub=hub, recorder=recorder, presence=presence)

    web = None
    if config.web.enabled:
        web = WebServer(engine, storage, config.web.host, config.web.port, config.web.token)
        web.start()

    stop = threading.Event()

    def handle_signal(signum, _frame):
        log.info("Signal %s empfangen", getattr(signal.Signals(signum), "name", signum))
        stop.set()

    # SIGTERM gibt es unter Windows zwar als Konstante, wird dort aber nie
    # zugestellt; einzelne Signale lassen sich je nach Plattform gar nicht
    # registrieren. Beides darf den Start nicht verhindern.
    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is None:
            continue
        try:
            signal.signal(signum, handle_signal)
        except (ValueError, OSError, AttributeError):  # pragma: no cover
            log.debug("Signal %s ist auf dieser Plattform nicht registrierbar", signum)

    try:
        engine.run(stop_event=stop)
    finally:
        if web is not None:
            web.stop()
        storage.close()
    return 0


def cmd_mode(args) -> int:
    """Modus über die REST-Schnittstelle setzen."""
    import requests

    config = read_config(args)
    mode = STATE_DISARMED if args.command == "disarm" else args.mode
    url = f"http://{config.web.host}:{config.web.port}/api/mode"
    headers = {"Authorization": f"Bearer {config.web.token}"} if config.web.token else {}
    try:
        response = requests.post(url, json={"mode": mode}, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def cmd_status(args) -> int:
    """Status über die REST-Schnittstelle abfragen."""
    import requests

    config = read_config(args)
    url = f"http://{config.web.host}:{config.web.port}/api/status"
    headers = {"Authorization": f"Bearer {config.web.token}"} if config.web.token else {}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        status = response.json()
    except requests.RequestException as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    print(f"Zustand : {status['state']} (Modus {status['mode']})")
    print(f"Bewegung: {'ja' if status['motion'] else 'nein'}  Score {status['score']:.2f}")
    print(f"Strecken: {status['link_count']}   Messungen: {status['ticks']}   "
          f"Fehler: {status['errors']}")
    print(f"Laufzeit: {_fmt_duration(status['uptime'])}")
    for link in status.get("links", [])[:10]:
        flag = "*" if link["triggered"] else " "
        print(f"  {flag} {(link['peer_name'] or link['peer_mac'])[:22]:22s} "
              f"Score {link['score']:4.2f}  z {link['z']:5.1f}  {link['band']}")
    return 0


def cmd_init_config(args) -> int:
    """Beispielkonfiguration schreiben."""
    target = Path(args.path)
    if target.exists() and not args.force:
        print(f"{target} existiert bereits (--force zum Überschreiben).", file=sys.stderr)
        return 1
    name = "config.full.example.yaml" if args.full else "config.example.yaml"
    template = Path(__file__).parent / name
    if not template.is_file():
        print(f"Vorlage nicht gefunden: {template}", file=sys.stderr)
        return 2
    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{target} angelegt. Jetzt Zugangsdaten eintragen und "
          f"'wlanalarm check -c {target}' ausführen.")
    if not args.full:
        print("Die Datei enthält bewusst nur das Nötigste - alles Weitere "
              "verwendet die eingebauten Vorgaben.")
        print("Vollständige Liste aller Einstellungen: init-config --full")
    return 0


# --------------------------------------------------------------------------- #
# Argumente
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wlanalarm",
        description="Bewegungs- und Einbruchserkennung über die WLAN-Funkstrecken "
                    "einer AVM FRITZ!Box.",
    )
    parser.add_argument("--version", action="version", version=f"WLANalarm {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str, config: bool = True):
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        if config:
            sub.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                             help=f"Konfigurationsdatei (Vorgabe: {DEFAULT_CONFIG})")
        sub.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
        sub.set_defaults(func=func)
        return sub

    add("check", cmd_check, "Konfiguration und FRITZ!Box-Verbindung prüfen")

    discover = add("discover", cmd_discover, "Verfügbare Funkstrecken auflisten")
    discover.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    discover.add_argument("--yaml", action="store_true",
                          help="fertigen links-Abschnitt für config.yaml erzeugen")

    calibrate_parser = add("calibrate", cmd_calibrate,
                           "Ruheverhalten vermessen (Wohnung dabei nicht betreten)")
    calibrate_parser.add_argument("--minutes", type=int, default=10,
                                  help="Messdauer in Minuten (Vorgabe: 10)")
    calibrate_parser.add_argument("--dry-run", action="store_true",
                                  help="nur anzeigen, nichts speichern")

    add("monitor", cmd_monitor, "Live-Anzeige im Terminal, ohne Alarm")

    diagnose = add("diagnose", cmd_diagnose,
                   "Zeigen, was die FRITZ!Box liefert (bei fehlenden Messwerten)")
    diagnose.add_argument("-o", "--output", help="Bericht zusätzlich in eine Datei schreiben")
    diagnose.add_argument("--raw", action="store_true",
                          help="rohes JSON ausgeben (enthält Gerätenamen und MAC-Adressen)")

    record = add("record", cmd_record, "Rohmesswerte mitschneiden")
    record.add_argument("--minutes", type=int, default=0,
                        help="Dauer in Minuten (0 = bis Strg-C)")
    record.add_argument("-o", "--output", help="Zielverzeichnis")
    record.add_argument("--compress", action="store_true", help="gzip-komprimiert schreiben")

    replay = add("replay", cmd_replay, "Aufzeichnung mit der aktuellen Konfiguration durchrechnen")
    replay.add_argument("file", help="aufgezeichnete .ndjson- oder .ndjson.gz-Datei")
    replay.add_argument("--use-baselines", action="store_true",
                        help="gespeicherte Kalibrierung verwenden")

    notify = add("test-notify", cmd_test_notify, "Testbenachrichtigung senden")
    notify.add_argument("--level", choices=["motion", "alarm"], default="alarm")

    add("run", cmd_run, "Dauerbetrieb starten")

    arm = add("arm", cmd_mode, "Anlage scharf schalten (über die REST-Schnittstelle)")
    arm.add_argument("mode", choices=sorted(ARMED_STATES))
    add("disarm", cmd_mode, "Anlage entschärfen (über die REST-Schnittstelle)")

    status = add("status", cmd_status, "Status des laufenden Dienstes abfragen")
    status.add_argument("--json", action="store_true", help="Ausgabe als JSON")

    init = add("init-config", cmd_init_config, "Beispielkonfiguration anlegen", config=False)
    init.add_argument("path", nargs="?", default=DEFAULT_CONFIG)
    init.add_argument("--force", action="store_true", help="vorhandene Datei überschreiben")
    init.add_argument("--full", action="store_true",
                      help="alle Einstellungen ausschreiben statt nur des Nötigsten")

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        setup_logging("INFO")
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    except SourceError as exc:
        print(f"Verbindungsfehler: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
