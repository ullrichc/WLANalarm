"""Datenquelle anhand der Konfiguration auswaehlen."""

from __future__ import annotations

import logging

from ..config import FritzboxConfig
from .base import SampleSource, SourceError
from .fritz_mesh import FritzMeshSource
from .fritz_tr064 import FritzTr064Source

log = logging.getLogger(__name__)


def build_source(config: FritzboxConfig) -> SampleSource:
    """Passende Quelle erzeugen.

    Bei ``source: auto`` wird zuerst die Mesh-Liste versucht - sie liefert
    echte dBm-Werte. Nur wenn die Box sie nicht anbietet, wird auf die
    deutlich groebere TR-064-Abfrage zurueckgefallen.
    """
    kwargs = {
        "address": config.address,
        "port": config.port,
        "username": config.username,
        "password": config.password,
        "use_tls": config.use_tls,
        "timeout": config.timeout,
    }

    if config.source == "tr064":
        return FritzTr064Source(**kwargs)
    if config.source == "mesh":
        return FritzMeshSource(**kwargs)

    mesh = FritzMeshSource(**kwargs)
    try:
        info = mesh.check()
        log.info("Mesh-Liste wird verwendet (%s)", info)
        return mesh
    except SourceError as exc:
        mesh.close()
        log.warning("Mesh-Liste nicht nutzbar (%s) - weiche auf TR-064 aus", exc)
        return FritzTr064Source(**kwargs)


def describe_source(source: SampleSource) -> str:
    """Kurzbeschreibung der Quelle samt Geraeteinfo, falls abfragbar."""
    check = getattr(source, "check", None)
    if check is None:
        return source.name
    try:
        return f"{source.name} ({check()})"
    except SourceError as exc:
        return f"{source.name} (nicht erreichbar: {exc})"
