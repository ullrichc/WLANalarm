"""Konfiguration: Laden, Validieren, Defaults.

Bewusst ohne pydantic - Dataclasses plus eine kleine Validierungsschicht
reichen und halten die Abhaengigkeiten klein genug fuer einen Raspberry Pi.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .model import normalise_mac


class ConfigError(ValueError):
    """Fehlerhafte oder unvollstaendige Konfiguration."""


_ENV_PATTERN = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


# --------------------------------------------------------------------------- #
# Teil-Konfigurationen
# --------------------------------------------------------------------------- #


@dataclass
class FritzboxConfig:
    """Zugang zur FRITZ!Box.

    Es wird ein FRITZ!Box-Benutzer mit der Berechtigung "FRITZ!Box Einstellungen"
    benoetigt; ausserdem muss unter Heimnetz > Netzwerk > Netzwerkeinstellungen
    der Punkt "Zugriff fuer Anwendungen zulassen" aktiv sein.
    """

    address: str = "http://fritz.box"
    port: int = 49000
    username: str = ""
    password: str = ""
    use_tls: bool = False
    #: Verbindungs-Timeout in Sekunden fuer TR-064 und den Mesh-Download.
    timeout: float = 10.0
    #: "mesh" = Mesh-Liste (RSSI in dBm, praeferiert),
    #: "tr064" = WLANConfiguration-Abfrage (nur Prozent, Fallback),
    #: "auto"  = Mesh versuchen, bei Fehler auf TR-064 zurueckfallen.
    source: str = "auto"

    def validate(self, path: str) -> None:
        if self.source not in ("auto", "mesh", "tr064"):
            raise ConfigError(f"{path}.source muss auto|mesh|tr064 sein, ist {self.source!r}")
        if not self.username:
            raise ConfigError(
                f"{path}.username fehlt - lege in der FRITZ!Box einen Benutzer mit "
                f"der Berechtigung 'FRITZ!Box Einstellungen' an"
            )
        if not self.password:
            raise ConfigError(f"{path}.password fehlt")
        if self.port <= 0 or self.port > 65535:
            raise ConfigError(f"{path}.port ungueltig: {self.port}")
        if self.timeout <= 0:
            raise ConfigError(f"{path}.timeout muss > 0 sein")


@dataclass
class SamplingConfig:
    """Wie oft und wie lange gemessen wird."""

    #: Abstand zwischen zwei Abfragen der FRITZ!Box in Sekunden. Die Box
    #: aktualisiert die Mesh-Liste nur alle 1-2 s; schneller zu pollen
    #: erzeugt Last ohne neue Information.
    interval: float = 2.0
    #: Kurzes Analysefenster ("passiert gerade etwas?") in Sekunden.
    window_seconds: float = 12.0
    #: Langes Fenster fuer die Ruhe-Baseline in Sekunden.
    baseline_seconds: float = 900.0
    #: Solange weniger Messpunkte als hier vorliegen, meldet der Detektor
    #: bewusst nichts (Aufwaermphase nach dem Start).
    warmup_samples: int = 20

    def validate(self, path: str) -> None:
        if self.interval < 0.5:
            raise ConfigError(f"{path}.interval unter 0.5 s ueberlastet die FRITZ!Box")
        if self.window_seconds < 3 * self.interval:
            raise ConfigError(
                f"{path}.window_seconds muss mindestens das Dreifache von interval sein"
            )
        if self.baseline_seconds <= self.window_seconds:
            raise ConfigError(f"{path}.baseline_seconds muss groesser als window_seconds sein")
        if self.warmup_samples < 5:
            raise ConfigError(f"{path}.warmup_samples muss >= 5 sein")


@dataclass
class DetectorConfig:
    """Schwellen der Bewegungserkennung.

    Der Detektor bildet je Funkstrecke eine Aktivitaetskennzahl in dB und
    vergleicht sie robust (Median/MAD) mit dem Ruheverhalten derselben Strecke.
    """

    #: Gewichte der Einzelmerkmale in der Aktivitaetskennzahl.
    weight_std: float = 1.0
    weight_jitter: float = 1.0
    weight_range: float = 0.25
    weight_rate: float = 0.5
    #: Gewicht des Signal-Rausch-Abstands, sofern die FRITZ!Box ihn liefert
    #: (ab Mesh-Schema 8.x als rx_rsni). Er reagiert auf Bewegung oft
    #: empfindlicher als die Feldstaerke, weil auch die Stoerleistung eingeht.
    weight_snr: float = 0.8
    #: Ab diesem robusten z-Wert gilt eine Strecke als voll ausgeschlagen (Score 1.0).
    z_full_scale: float = 8.0
    #: Untergrenze fuer die Streuung der Baseline in dB. Verhindert, dass eine
    #: extrem ruhige Strecke schon bei Zehntel-dB in die Saettigung laeuft.
    min_baseline_scale_db: float = 0.35
    #: Die Aktivitaet muss die Ruhe-Baseline um mindestens so viel dB uebersteigen.
    min_delta_db: float = 0.5
    #: Ab diesem Score gilt eine einzelne Strecke als ausgeloest.
    trigger_score: float = 0.55
    #: Unter diesem Score gilt sie wieder als ruhig (Hysterese).
    clear_score: float = 0.30
    #: So viele Strecken muessen gleichzeitig ausloesen ...
    min_links: int = 2
    #: ... es sei denn, eine einzelne Strecke erreicht diesen Score.
    strong_score: float = 0.85
    #: Ob ein einzelner sehr starker Ausschlag auch dann genuegt, wenn genug
    #: andere Strecken zur Verfuegung stehen und ruhig bleiben. Standardmaessig
    #: aus: ein einzelnes auffaelliges Geraet (Firmware-Update, Stromsparmodus,
    #: Kanalwechsel) ist die haeufigste Fehlalarmquelle. Stehen ohnehin weniger
    #: als `min_links` Strecken bereit, greift die Regel automatisch.
    allow_single_strong: bool = False
    #: So viele aufeinanderfolgende Ticks muss die Bedingung halten.
    trigger_consecutive: int = 2
    #: So lange muss Ruhe herrschen, bis "keine Bewegung" gemeldet wird.
    clear_seconds: float = 25.0
    #: Strecken, deren Ruhe-Streuung darueber liegt, sind als Sensor unbrauchbar
    #: (typisch: Handys, Notebooks, alles was sich bewegt oder schlafen legt).
    max_baseline_activity_db: float = 6.0
    #: Verschwindet eine Strecke aus der Mesh-Liste, wird ihr Zustand nach so
    #: vielen Sekunden verworfen.
    stale_after_seconds: float = 120.0

    def validate(self, path: str) -> None:
        if not 0 < self.trigger_score <= 1:
            raise ConfigError(f"{path}.trigger_score muss in (0, 1] liegen")
        if not 0 <= self.clear_score < self.trigger_score:
            raise ConfigError(f"{path}.clear_score muss >= 0 und < trigger_score sein")
        if self.min_links < 1:
            raise ConfigError(f"{path}.min_links muss >= 1 sein")
        if self.trigger_consecutive < 1:
            raise ConfigError(f"{path}.trigger_consecutive muss >= 1 sein")
        if self.z_full_scale <= 0:
            raise ConfigError(f"{path}.z_full_scale muss > 0 sein")
        if self.min_baseline_scale_db <= 0:
            raise ConfigError(f"{path}.min_baseline_scale_db muss > 0 sein")


@dataclass
class LinkConfig:
    """Feineinstellung fuer eine einzelne Funkstrecke bzw. ein Geraet."""

    #: MAC der Gegenstelle. Trifft auf alle Baender dieses Geraets zu.
    mac: str = ""
    #: Klarname fuer Meldungen; ueberschreibt den Namen aus der FRITZ!Box.
    name: str = ""
    #: Zone, z.B. "flur" oder "wohnzimmer".
    zone: str = "default"
    #: Gewicht in der Gesamtbewertung (0 = beobachten, aber nie ausloesen).
    weight: float = 1.0
    #: Strecke komplett ignorieren.
    ignore: bool = False
    #: Individuelle Ausloeseschwelle; None = Wert aus DetectorConfig.
    trigger_score: float | None = None

    def __post_init__(self) -> None:
        self.mac = normalise_mac(self.mac)

    def validate(self, path: str) -> None:
        if not self.mac:
            raise ConfigError(f"{path}.mac fehlt")
        if self.weight < 0:
            raise ConfigError(f"{path}.weight darf nicht negativ sein")


@dataclass
class SelectionConfig:
    """Welche Funkstrecken ueberhaupt als Sensor taugen.

    Die Voreinstellungen bilden nach, was Comcast seinen Kundinnen und Kunden
    empfiehlt: nur stationaere, dauerhaft eingeschaltete Geraete verwenden.
    """

    #: Nur diese Baender verwenden (leer = alle, so die Voreinstellung).
    #:
    #: 5 und 6 GHz reagieren wegen der kuerzeren Wellenlaenge empfindlicher auf
    #: Bewegung. Das ist aber ein Grund, sie zu *bevorzugen*, nicht 2,4 GHz
    #: auszuschliessen: In vielen Haushalten haengen ausgerechnet die
    #: brauchbaren, dauerhaft aktiven Geraete - Steckdosen, Luftreiniger, Sensoren - nur im
    #: 2,4-GHz-Netz, weil sie nichts anderes koennen. Die Bandvorliebe steckt
    #: deshalb in der Rangfolge (siehe selection._rank), nicht in einem Filter.
    bands: list[str] = field(default_factory=list)
    #: Strecken zu Mesh-Repeatern immer mitnehmen - sie sind die stabilsten
    #: und damit besten virtuellen Sensoren im Haus.
    always_use_mesh_nodes: bool = True
    #: Nur explizit in `links` aufgefuehrte Geraete verwenden.
    only_configured: bool = False
    #: Obergrenze aktiver Sensorstrecken (Comcast erlaubt drei Geraete plus
    #: Repeater; hier ist es nur eine Bremse gegen unnoetige Rechenlast).
    max_links: int = 12
    #: Gegenstellen, deren Name auf eines dieser Muster passt, werden ignoriert
    #: (Kleinschreibung, einfache Teilstring-Suche).
    #:
    #: Diese Liste kann grundsaetzlich nicht vollstaendig sein - Geraetenamen
    #: sind frei waehlbar. Sie faengt die haeufigen Faelle ab; das letzte Wort
    #: hat die Kalibrierung, die das Ruheverhalten tatsaechlich misst.
    ignore_name_contains: list[str] = field(
        default_factory=lambda: [
            # Smartphones
            "iphone", "android", "pixel", "galaxy", "oneplus", "xperia",
            "huawei", "fairphone", "handy", "phone",
            # Tablets
            "ipad", "-pad", "tablet", "tab-", "kindle",
            # Rechner
            "notebook", "laptop", "macbook", "thinkpad", "surface",
            # Am Koerper getragen und Spielgeraete
            "watch", "buds", "airpods", "switch", "steamdeck", "deck-",
        ]
    )
    #: Namensmuster, die auf ein persoenliches Geraet hindeuten, etwa
    #: "Handy-von-Alex". Wer sein Geraet nach sich benennt, traegt es auch mit
    #: sich herum.
    ignore_personal_names: bool = True
    #: Diese MAC-Adressen nie als Sensor verwenden.
    ignore_macs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ignore_macs = [normalise_mac(m) for m in self.ignore_macs]
        self.ignore_name_contains = [s.lower() for s in self.ignore_name_contains]

    def validate(self, path: str) -> None:
        if self.max_links < 1:
            raise ConfigError(f"{path}.max_links muss >= 1 sein")


@dataclass
class ScheduleConfig:
    """Zeitgesteuertes Scharfschalten (Comcasts 'Nachtwache')."""

    enabled: bool = False
    #: Uhrzeit HH:MM, ab der automatisch scharf geschaltet wird.
    arm_at: str = "23:00"
    #: Uhrzeit HH:MM, ab der automatisch entschaerft wird.
    disarm_at: str = "06:30"
    #: Modus, in den `arm_at` schaltet.
    arm_mode: str = "armed_night"
    #: Wochentage 0=Montag .. 6=Sonntag; leer = jeden Tag.
    weekdays: list[int] = field(default_factory=list)

    def validate(self, path: str) -> None:
        if not self.enabled:
            return
        for key in ("arm_at", "disarm_at"):
            value = getattr(self, key)
            if not re.fullmatch(r"[0-2]\d:[0-5]\d", value or ""):
                raise ConfigError(f"{path}.{key} muss im Format HH:MM vorliegen, ist {value!r}")
        if self.arm_mode not in ARMED_MODES:
            raise ConfigError(f"{path}.arm_mode muss einer von {sorted(ARMED_MODES)} sein")
        for day in self.weekdays:
            if not 0 <= day <= 6:
                raise ConfigError(f"{path}.weekdays enthaelt ungueltigen Tag {day}")


@dataclass
class PresenceConfig:
    """Automatisches Scharf-/Unscharfschalten anhand anwesender Geraete.

    Achtung: moderne Smartphones nutzen zufaellige MAC-Adressen. Fuer die
    hinterlegten Geraete muss die MAC-Randomisierung fuer das eigene WLAN
    abgeschaltet sein, sonst funktioniert das nicht zuverlaessig.
    """

    enabled: bool = False
    #: MACs der Anwesenheitsmelder (Smartphones der Bewohner).
    macs: list[str] = field(default_factory=list)
    #: Modus, wenn niemand da ist.
    away_mode: str = "armed_away"
    #: Modus, wenn jemand da ist. "disarmed" oder "armed_home".
    home_mode: str = "disarmed"
    #: Erst nach so vielen Sekunden ohne jedes Geraet gilt "niemand da"
    #: (WLAN-Abmeldungen sind fluechtig).
    away_after_seconds: float = 300.0

    def __post_init__(self) -> None:
        self.macs = [normalise_mac(m) for m in self.macs]

    def validate(self, path: str) -> None:
        if not self.enabled:
            return
        if not self.macs:
            raise ConfigError(f"{path}.macs darf bei enabled=true nicht leer sein")
        if self.away_mode not in ARMED_MODES:
            raise ConfigError(f"{path}.away_mode muss einer von {sorted(ARMED_MODES)} sein")
        if self.home_mode not in ARMED_MODES | {"disarmed"}:
            raise ConfigError(f"{path}.home_mode ist ungueltig: {self.home_mode!r}")
        if self.away_after_seconds < 0:
            raise ConfigError(f"{path}.away_after_seconds darf nicht negativ sein")


@dataclass
class AlarmConfig:
    """Verhalten der Alarmanlage."""

    #: Startmodus beim Programmstart.
    initial_mode: str = "disarmed"
    #: Verzoegerung vor dem Scharfschalten (Zeit zum Verlassen der Wohnung).
    exit_delay: float = 45.0
    #: Verzoegerung zwischen erster Bewegung und Alarm (Zeit zum Entschaerfen).
    entry_delay: float = 30.0
    #: Nach einem Alarm so lange keinen neuen Alarm ausloesen.
    cooldown: float = 300.0
    #: Zonen, die im jeweiligen Modus ueberwacht werden (leer = alle).
    zones_armed_home: list[str] = field(default_factory=list)
    zones_armed_night: list[str] = field(default_factory=list)
    zones_armed_away: list[str] = field(default_factory=list)
    #: Auch ohne scharfe Anlage Bewegungsmeldungen verschicken.
    notify_motion_when_disarmed: bool = False
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    presence: PresenceConfig = field(default_factory=PresenceConfig)

    def validate(self, path: str) -> None:
        if self.initial_mode not in ARMED_MODES | {"disarmed"}:
            raise ConfigError(f"{path}.initial_mode ist ungueltig: {self.initial_mode!r}")
        for key in ("exit_delay", "entry_delay", "cooldown"):
            if getattr(self, key) < 0:
                raise ConfigError(f"{path}.{key} darf nicht negativ sein")
        self.schedule.validate(f"{path}.schedule")
        self.presence.validate(f"{path}.presence")

    def zones_for_mode(self, mode: str) -> list[str]:
        return {
            "armed_home": self.zones_armed_home,
            "armed_night": self.zones_armed_night,
            "armed_away": self.zones_armed_away,
        }.get(mode, [])


@dataclass
class NotifierConfig:
    """Ein Benachrichtigungskanal. `type` waehlt die Implementierung."""

    type: str = "log"
    enabled: bool = True
    name: str = ""
    #: Nur Ereignisse ab diesem Rang senden: motion < armed_motion < alarm.
    min_level: str = "alarm"
    #: Kanalspezifische Einstellungen (URL, Token, Topic, ...).
    options: dict[str, Any] = field(default_factory=dict)

    def validate(self, path: str) -> None:
        if self.min_level not in NOTIFY_LEVELS:
            raise ConfigError(
                f"{path}.min_level muss einer von {sorted(NOTIFY_LEVELS)} sein, "
                f"ist {self.min_level!r}"
            )


@dataclass
class WebConfig:
    """Eingebautes Dashboard und REST-Schnittstelle."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8723
    #: Token fuer schreibende Zugriffe (scharf/unscharf). Leer = keine Absicherung,
    #: dann sollte host auf 127.0.0.1 bleiben.
    token: str = ""

    def validate(self, path: str) -> None:
        if not 0 < self.port <= 65535:
            raise ConfigError(f"{path}.port ungueltig: {self.port}")
        if self.enabled and self.host not in ("127.0.0.1", "localhost", "::1") and not self.token:
            raise ConfigError(
                f"{path}: bei host={self.host!r} muss ein token gesetzt sein, "
                f"sonst kann jeder im Netz die Anlage entschaerfen"
            )


@dataclass
class StorageConfig:
    """Ablage von Zustand, Ereignissen und optionalen Rohdaten."""

    directory: str = "./state"
    #: Ereignisse laenger als so viele Tage werden geloescht.
    event_retention_days: int = 90
    #: Rohmesswerte mitschreiben (fuer spaeteres Nachjustieren per `replay`).
    record_samples: bool = False
    #: Aufzeichnungen aelter als so viele Tage werden geloescht.
    sample_retention_days: int = 3

    def validate(self, path: str) -> None:
        if self.event_retention_days < 1:
            raise ConfigError(f"{path}.event_retention_days muss >= 1 sein")

    @property
    def path(self) -> Path:
        return Path(self.directory).expanduser()


@dataclass
class Config:
    """Gesamtkonfiguration."""

    fritzbox: FritzboxConfig = field(default_factory=FritzboxConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    alarm: AlarmConfig = field(default_factory=AlarmConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)
    links: list[LinkConfig] = field(default_factory=list)
    notifiers: list[NotifierConfig] = field(default_factory=list)
    log_level: str = "INFO"

    def validate(self) -> "Config":
        self.fritzbox.validate("fritzbox")
        self.sampling.validate("sampling")
        self.detector.validate("detector")
        self.selection.validate("selection")
        self.alarm.validate("alarm")
        self.storage.validate("storage")
        self.web.validate("web")
        seen: set[str] = set()
        for index, link in enumerate(self.links):
            link.validate(f"links[{index}]")
            if link.mac in seen:
                raise ConfigError(f"links[{index}]: MAC {link.mac} ist mehrfach konfiguriert")
            seen.add(link.mac)
        for index, notifier in enumerate(self.notifiers):
            notifier.validate(f"notifiers[{index}]")
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ConfigError(f"log_level ungueltig: {self.log_level!r}")
        return self

    def link_config(self, mac: str) -> LinkConfig | None:
        target = normalise_mac(mac)
        for link in self.links:
            if link.mac == target:
                return link
        return None


ARMED_MODES = {"armed_home", "armed_away", "armed_night"}
NOTIFY_LEVELS = ("motion", "armed_motion", "alarm")


# --------------------------------------------------------------------------- #
# Laden
# --------------------------------------------------------------------------- #


def expand_env(value: Any) -> Any:
    """`${env:NAME}` bzw. `${env:NAME:fallback}` aus der Umgebung ersetzen.

    So bleiben Zugangsdaten aus der YAML-Datei heraus und koennen z.B. per
    systemd `EnvironmentFile` oder Docker-Secret gesetzt werden.
    """
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            if resolved is None:
                if default is None:
                    raise ConfigError(
                        f"Umgebungsvariable {name} ist nicht gesetzt "
                        f"(referenziert als ${{env:{name}}})"
                    )
                resolved = default
            return resolved

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


#: Verschachtelte Abschnitte. Wegen ``from __future__ import annotations`` sind
#: die Feldtypen zur Laufzeit nur Strings, deshalb hier explizit gefuehrt.
_NESTED: dict[type, dict[str, type]] = {
    Config: {
        "fritzbox": FritzboxConfig,
        "sampling": SamplingConfig,
        "detector": DetectorConfig,
        "selection": SelectionConfig,
        "alarm": AlarmConfig,
        "storage": StorageConfig,
        "web": WebConfig,
    },
    AlarmConfig: {
        "schedule": ScheduleConfig,
        "presence": PresenceConfig,
    },
}


def _build(cls, data: Any, path: str):
    """Dataclass rekursiv aus einem Dict bauen und unbekannte Schluessel melden."""
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: erwartet wurde ein Abschnitt, gefunden {type(data).__name__}")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"{path}: unbekannte Schluessel {sorted(unknown)}; "
            f"erlaubt sind {sorted(known)}"
        )
    nested = _NESTED.get(cls, {})
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        if name in nested:
            kwargs[name] = _build(nested[name], value, f"{path}.{name}")
        else:
            kwargs[name] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:  # pragma: no cover - defensiv
        raise ConfigError(f"{path}: {exc}") from exc


def load_config(path: str | Path) -> Config:
    """Konfiguration aus einer YAML-Datei laden und validieren."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {file_path}")
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file_path}: YAML-Fehler: {exc}") from exc
    return config_from_dict(raw)


def config_from_dict(raw: dict) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("Die Konfiguration muss ein YAML-Mapping sein")
    raw = expand_env(raw)
    links_raw = raw.pop("links", []) or []
    notifiers_raw = raw.pop("notifiers", []) or []
    config = _build(Config, raw, "<root>")
    config.links = [_build(LinkConfig, item, f"links[{i}]") for i, item in enumerate(links_raw)]
    config.notifiers = [
        _build(NotifierConfig, item, f"notifiers[{i}]") for i, item in enumerate(notifiers_raw)
    ]
    return config.validate()
