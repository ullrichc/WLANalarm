"""Kleiner HTTP-Server fuer Dashboard und REST-Schnittstelle.

Bewusst auf der Standardbibliothek aufgebaut - ein Webframework waere fuer
fuenf Endpunkte unverhaeltnismaessig und wuerde die Installation auf einem
Raspberry Pi unnoetig aufblaehen.

Sicherheitshinweis: wer den Server ueber 127.0.0.1 hinaus oeffnet, muss ein
Token setzen. Ohne Token koennte sonst jedes Geraet im Heimnetz die Anlage
entschaerfen. Die Konfiguration erzwingt das.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..alarm import ARMED_STATES, STATE_DISARMED

log = logging.getLogger(__name__)

_STATIC = Path(__file__).parent / "static"


class WebServer:
    """Startet den HTTP-Server in einem Hintergrundthread."""

    def __init__(self, engine, storage, host: str, port: int, token: str = "") -> None:
        self._engine = engine
        self._storage = storage
        self._token = token
        handler = _make_handler(engine, storage, token)
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="wlanalarm-web", daemon=True
        )

    @property
    def address(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> None:
        self._thread.start()
        log.info("Dashboard erreichbar unter %s", self.address)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def _make_handler(engine, storage, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WLANalarm"
        protocol_version = "HTTP/1.1"

        # -- Hilfen -------------------------------------------------------- #

        def log_message(self, fmt: str, *args) -> None:  # pragma: no cover
            log.debug("web: " + fmt, *args)

        def _authorised(self) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                return secrets.compare_digest(header[7:], token)
            query = parse_qs(urlparse(self.path).query)
            supplied = (query.get("token") or [""])[0]
            return bool(supplied) and secrets.compare_digest(supplied, token)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        # -- Routen -------------------------------------------------------- #

        def do_GET(self) -> None:  # noqa: N802 - von BaseHTTPRequestHandler vorgegeben
            route = urlparse(self.path).path.rstrip("/") or "/"
            query = parse_qs(urlparse(self.path).query)

            if route == "/api/health":
                zustand, ok = _gesundheit(engine)
                self._json(200 if ok else 503, zustand)
                return
            if not self._authorised():
                self._error(401, "Token fehlt oder ist falsch")
                return
            if route == "/":
                self._serve_index()
            elif route == "/api/status":
                self._json(200, engine.snapshot())
            elif route == "/api/events":
                limit = _int(query.get("limit"), 50, 1, 500)
                level = (query.get("level") or [None])[0]
                if storage is None:
                    self._json(200, [])
                else:
                    self._json(200, storage.recent_events(limit=limit, min_level=level))
            elif route == "/api/links":
                self._json(200, [c.to_dict() for c in engine.candidates])
            else:
                self._error(404, "Unbekannter Pfad")

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def _herkunft_ok(self) -> bool:
            """Schreibende Zugriffe gegen fremde Webseiten absichern.

            Ohne diese Pruefung koennte eine beliebige Seite, die im Browser
            desselben Rechners geoeffnet ist, per Formular-POST die Anlage
            entschaerfen - im vorgesehenen Betrieb auf 127.0.0.1 ganz ohne
            Token. Verlangt werden deshalb ein JSON-Inhaltstyp, den ein
            einfaches Formular nicht setzen kann, und eine Herkunft, die zum
            eigenen Server passt.
            """
            typ = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if typ != "application/json":
                return False
            herkunft = self.headers.get("Origin") or self.headers.get("Referer")
            if not herkunft:
                return True  # Aufrufe ohne Browser, etwa curl oder die eigene CLI
            eigen = self.headers.get("Host", "")
            return urlparse(herkunft).netloc == eigen

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path.rstrip("/")
            if not self._authorised():
                self._error(401, "Token fehlt oder ist falsch")
                return
            if not self._herkunft_ok():
                self._error(
                    403,
                    "Abgelehnt: Anfrage muss Content-Type application/json tragen "
                    "und von dieser Oberflaeche stammen",
                )
                return
            if route != "/api/mode":
                self._error(404, "Unbekannter Pfad")
                return

            length = _int([self.headers.get("Content-Length", "0")], 0, 0, 65536)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._error(400, "Ungueltiges JSON")
                return

            mode = payload.get("mode")
            if mode not in ARMED_STATES and mode != STATE_DISARMED:
                self._error(
                    400,
                    f"mode muss einer von {sorted(list(ARMED_STATES) + [STATE_DISARMED])} sein",
                )
                return
            try:
                status = engine.set_mode(mode, source="web")
            except ValueError as exc:  # pragma: no cover - oben schon geprueft
                self._error(400, str(exc))
                return
            self._json(200, status)

        def _serve_index(self) -> None:
            path = _STATIC / "index.html"
            try:
                body = path.read_bytes()
            except OSError:
                self._error(500, "Dashboard-Datei fehlt")
                return
            self._send(200, body, "text/html; charset=utf-8")

    return Handler


#: Kommt so lange keine Messung zustande, gilt der Dienst als gestoert.
#: Grosszuegig bemessen, damit ein einzelner Aussetzer nicht sofort Alarm
#: schlaegt - aber eng genug, um einen echten Ausfall binnen Minuten zu zeigen.
STILLSTAND_SEKUNDEN = 120.0
#: So viele Messfehler in Folge gelten als anhaltende Stoerung.
MAX_FEHLER_IN_FOLGE = 5


def _gesundheit(engine) -> tuple[dict, bool]:
    """Tatsaechlichen Betriebszustand ermitteln.

    Ein Gesundheitsendpunkt, der nur bestaetigt, dass der Webserver laeuft,
    ist fuer eine Alarmanlage schlimmer als keiner: Er meldet Betriebsbereit-
    schaft, waehrend die Messung seit Stunden steht. Geprueft wird deshalb,
    ob ueberhaupt noch gemessen wird.
    """
    zustand = engine.snapshot(brief=True)
    # Das Alter kommt aus der Engine: nur sie kennt ihre Zeitbasis.
    alter = zustand.get("last_scan_age")
    fehler = zustand.get("consecutive_errors", 0)

    gruende = []
    if zustand["ticks"] == 0 and (zustand.get("uptime") or 0) > STILLSTAND_SEKUNDEN:
        gruende.append("seit dem Start keine einzige Messung zustande gekommen")
    elif alter is not None and alter > STILLSTAND_SEKUNDEN:
        gruende.append(f"letzte Messung vor {alter:.0f} s")
    if fehler >= MAX_FEHLER_IN_FOLGE:
        gruende.append(f"{fehler} Messfehler in Folge")

    return (
        {
            "status": "ok" if not gruende else "degraded",
            "reasons": gruende,
            "last_scan_age": round(alter, 1) if alter is not None else None,
            "consecutive_errors": fehler,
            "ticks": zustand["ticks"],
            "state": zustand["state"],
        },
        not gruende,
    )


def _int(values, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int((values or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))
