"""Gemeinsame Schnittstelle aller Datenquellen."""

from __future__ import annotations

import abc

from ..model import Scan


class SourceError(RuntimeError):
    """Die Quelle konnte keine Messwerte liefern."""


class SampleSource(abc.ABC):
    """Liefert auf Abruf einen Satz Funkstrecken-Messwerte."""

    #: Sprechender Name fuer Logausgaben.
    name: str = "source"

    @abc.abstractmethod
    def scan(self) -> Scan:
        """Einen Messzyklus ausfuehren.

        Raises:
            SourceError: wenn die Quelle vorruebergehend nicht erreichbar ist.
                Die Engine faengt das ab und versucht es beim naechsten Tick erneut.
        """

    def close(self) -> None:
        """Ressourcen freigeben. Standard: nichts zu tun."""

    def __enter__(self) -> "SampleSource":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
