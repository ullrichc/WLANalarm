"""Parser fuer die Mesh-Topologie-Liste der FRITZ!Box.

Die Box liefert unter dem von ``Hosts:1 X_AVM-DE_GetMeshListPath`` genannten
Pfad ein JSON-Dokument mit dem Aufbau::

    nodes[]                      # FRITZ!Box, Repeater und alle Clients
      node_interfaces[]          # LAN-, WLAN- und Gastnetz-Schnittstellen
        node_links[]             # die tatsaechlichen Verbindungen

Jede Verbindung taucht zweimal auf - einmal aus Sicht des Access Points und
einmal aus Sicht der Gegenstelle. Der Parser fuehrt beide Sichten zusammen und
bevorzugt die Sicht mit tatsaechlichem RSSI-Wert.

Der Parser ist bewusst frei von Netzwerkzugriffen, damit er sich mit
aufgezeichneten JSON-Dateien testen laesst.
"""

from __future__ import annotations

from typing import Any

from ..model import (
    BAND_5,
    BAND_6,
    BAND_24,
    BAND_UNKNOWN,
    LinkSample,
    Scan,
    make_link_id,
    normalise_mac,
)

#: Feldnamen, unter denen verschiedene FRITZ!OS-Versionen die Empfangsleistung
#: ablegen. Bis Mesh-Schema 1.x hiess das Feld schlicht ``rssi``; ab Schema 8.x
#: (FRITZ!OS 8.2x) verwendet AVM die Bezeichnungen aus IEEE 802.11k: ``rx_rcpi``
#: fuer die Empfangsleistung und ``rx_rsni`` fuer den Stoerabstand.
_RSSI_KEYS = ("rssi", "cur_rssi", "signal_strength", "rx_rssi", "cur_signal_strength")
#: RCPI-Felder werden getrennt gefuehrt, weil sie anders zu lesen sind: nach
#: IEEE 802.11k steht dort 0..220 mit ``dBm = RCPI / 2 - 110``. Ein Wert wie 58
#: bedeutet in einem rssi-Feld -58 dBm, in einem rcpi-Feld dagegen -81 dBm -
#: die Bedeutung haengt am Feldnamen, nicht am Zahlenwert.
_RCPI_KEYS = ("rx_rcpi", "tx_rcpi")
#: Signal-Rausch-Abstand, ebenfalls aus IEEE 802.11k.
_SNR_KEYS = ("rx_rsni", "tx_rsni", "snr")
#: IEEE 802.11k kennzeichnet einen nicht messbaren Wert mit 255.
_NICHT_VERFUEGBAR = 255
_MCS_KEYS = ("cur_rx_mcs", "cur_tx_mcs", "rx_mcs", "tx_mcs")
_NSS_KEYS = ("cur_rx_nss", "cur_tx_nss", "rx_streams", "tx_streams", "streams_rx")


def parse_mesh_list(data: dict, ts: float) -> Scan:
    """Mesh-JSON in einen :class:`Scan` uebersetzen."""
    nodes = {node.get("uid"): node for node in data.get("nodes", []) if isinstance(node, dict)}
    interfaces: dict[str, tuple[dict, dict]] = {}
    for node in nodes.values():
        for iface in node.get("node_interfaces", []) or []:
            if isinstance(iface, dict) and iface.get("uid"):
                interfaces[iface["uid"]] = (node, iface)

    best: dict[str, tuple[int, LinkSample]] = {}
    for node, iface in interfaces.values():
        if _iface_type(iface) != "WLAN":
            continue
        for link in iface.get("node_links", []) or []:
            if not isinstance(link, dict):
                continue
            sample = _build_sample(link, nodes, interfaces, ts)
            if sample is None:
                continue
            # Rang: Eintrag mit RSSI schlaegt Eintrag ohne; bei Gleichstand
            # gewinnt die Sicht des Access Points.
            rank = (2 if sample.rssi_dbm is not None else 0) + (1 if _is_ap(iface) else 0)
            previous = best.get(sample.link_id)
            if previous is None or rank > previous[0]:
                best[sample.link_id] = (rank, sample)

    return Scan(ts=ts, samples=[sample for _, sample in best.values()])


def _build_sample(
    link: dict,
    nodes: dict[str, dict],
    interfaces: dict[str, tuple[dict, dict]],
    ts: float,
) -> LinkSample | None:
    if str(link.get("type", "WLAN")).upper() not in ("WLAN", "WIFI", ""):
        return None
    state = str(link.get("state", "CONNECTED")).upper()
    if state not in ("CONNECTED", "ACTIVE", "UP", ""):
        return None

    end_a = interfaces.get(link.get("node_interface_1_uid"))
    end_b = interfaces.get(link.get("node_interface_2_uid"))
    if end_a is None or end_b is None:
        return None

    # Access-Point-Seite bestimmen: bevorzugt ueber den Betriebsmodus des
    # Interfaces, ersatzweise ueber die Mesh-Rolle des Knotens.
    if _is_ap(end_a[1]) and not _is_ap(end_b[1]):
        ap_node, ap_iface = end_a
        peer_node, peer_iface = end_b
    elif _is_ap(end_b[1]) and not _is_ap(end_a[1]):
        ap_node, ap_iface = end_b
        peer_node, peer_iface = end_a
    elif _mesh_rank(end_a[0]) >= _mesh_rank(end_b[0]):
        ap_node, ap_iface = end_a
        peer_node, peer_iface = end_b
    else:
        ap_node, ap_iface = end_b
        peer_node, peer_iface = end_a

    ap_mac = normalise_mac(ap_iface.get("mac_address") or ap_node.get("device_mac_address"))
    peer_mac = normalise_mac(peer_iface.get("mac_address") or peer_node.get("device_mac_address"))
    if not ap_mac or not peer_mac or ap_mac == peer_mac:
        return None

    band = detect_band(ap_iface) or detect_band(peer_iface) or BAND_UNKNOWN

    return LinkSample(
        ts=ts,
        link_id=make_link_id(ap_mac, peer_mac, band),
        ap_mac=ap_mac,
        ap_name=_device_name(ap_node),
        peer_mac=peer_mac,
        peer_name=_device_name(peer_node),
        band=band,
        rssi_dbm=_rssi(link),
        snr_db=_snr(link),
        rx_rate_kbps=_num(link.get("cur_data_rate_rx")),
        tx_rate_kbps=_num(link.get("cur_data_rate_tx")),
        mcs=_first_int(link, _MCS_KEYS),
        streams=_first_int(link, _NSS_KEYS),
        peer_is_mesh_node=_is_mesh_node(peer_node),
    )


def detect_band(iface: dict) -> str | None:
    """Frequenzband einer WLAN-Schnittstelle bestimmen.

    Reihenfolge: ``supported_bands`` (eindeutig), dann der Schnittstellenname,
    zuletzt die Kanalnummer. Die Kanalnummer ist nur ein Notbehelf, weil sich
    2,4 GHz und 6 GHz im Nummernraum ueberschneiden.
    """
    # Die Mittenfrequenz ist die einzige eindeutige Quelle: Kanalnummern
    # ueberschneiden sich zwischen 2,4 und 6 GHz, und ab Mesh-Schema 8.x fehlt
    # das Feld supported_bands ganz.
    frequenz = _num((iface.get("current_channel_info") or {}).get("primary_freq"))
    if frequenz is None:
        kanalliste = iface.get("channel_list") or []
        if isinstance(kanalliste, list) and kanalliste:
            aktuell = _num(iface.get("current_channel"))
            for eintrag in kanalliste:
                if isinstance(eintrag, dict) and _num(eintrag.get("channel")) == aktuell:
                    frequenz = _num(eintrag.get("frequency"))
                    break
    if frequenz is not None:
        # Angabe in kHz; zur Sicherheit auch MHz zulassen.
        mhz = frequenz / 1000 if frequenz > 100_000 else frequenz
        if 2400 <= mhz <= 2500:
            return BAND_24
        if 5150 <= mhz <= 5900:
            return BAND_5
        if 5925 <= mhz <= 7125:
            return BAND_6

    bands = iface.get("supported_bands") or iface.get("bands") or []
    if isinstance(bands, str):
        bands = [bands]
    joined = " ".join(str(b) for b in bands).upper()
    if "6G" in joined or "6_G" in joined:
        return BAND_6
    if "5G" in joined or "5_G" in joined:
        return BAND_5
    if "2_4G" in joined or "24G" in joined or "2.4G" in joined:
        return BAND_24

    name = str(iface.get("name", "")).upper().replace(" ", "")
    if "6GHZ" in name or "6G" in name:
        return BAND_6
    if "5GHZ" in name or "5G" in name:
        return BAND_5
    if "2.4GHZ" in name or "2,4GHZ" in name or "24GHZ" in name or ":2G" in name:
        return BAND_24

    channel = _num(iface.get("current_channel"))
    if channel is not None:
        if 36 <= channel <= 196:
            return BAND_5
        if 1 <= channel <= 14:
            return BAND_24
    return None


def _iface_type(iface: dict) -> str:
    return str(iface.get("type", "")).upper()


def _is_ap(iface: dict) -> bool:
    opmode = str(iface.get("opmode", "")).upper()
    return "AP" in opmode and "CLIENT" not in opmode and "STA" not in opmode


def _is_mesh_node(node: dict) -> bool:
    role = str(node.get("mesh_role", "")).lower()
    return bool(node.get("is_meshed")) and role in ("master", "slave")


def _mesh_rank(node: dict) -> int:
    """Je hoeher, desto eher ist der Knoten die Access-Point-Seite."""
    role = str(node.get("mesh_role", "")).lower()
    return {"master": 2, "slave": 1}.get(role, 0)


def _device_name(node: dict) -> str:
    for key in ("device_name", "device_model", "device_mac_address"):
        value = node.get(key)
        if value:
            return str(value)
    return ""


def _rssi(link: dict) -> float | None:
    """Empfangsleistung in dBm, quer ueber alle bekannten Feldnamen.

    Drei Schreibweisen kommen in freier Wildbahn vor:

    * bereits in dBm, negativ (AVM liefert ``rx_rcpi`` so),
    * als Betrag ohne Vorzeichen,
    * als RCPI im Rohformat nach IEEE 802.11k, also 0..220 mit
      ``dBm = RCPI / 2 - 110``.

    Der Wert 255 bedeutet nach derselben Norm "nicht messbar" - so kennzeichnet
    AVM etwa die Senderichtung, die die Box nicht beobachten kann.
    """
    for key in _RSSI_KEYS:
        value = _num(link.get(key))
        if value is None or value >= _NICHT_VERFUEGBAR:
            continue
        # Manche Firmwares liefern den Betrag ohne Vorzeichen.
        gemessen = value if value < 0 else -value
        if -110 <= gemessen <= -10:
            return gemessen

    for key in _RCPI_KEYS:
        value = _num(link.get(key))
        if value is None or value >= _NICHT_VERFUEGBAR:
            continue
        # AVM liefert hier bereits dBm; das Rohformat der Norm faengt der
        # zweite Zweig ab.
        gemessen = value if value < 0 else value / 2 - 110
        if -110 <= gemessen <= -10:
            return gemessen
    return None


def _snr(link: dict) -> float | None:
    """Signal-Rausch-Abstand in dB.

    AVM liefert ``rx_rsni`` direkt in dB (Werte um 30 passen zur Differenz aus
    Empfangsleistung und dem Rauschpegel ``anpi`` der Schnittstelle). Das
    Rohformat nach IEEE 802.11k waere ``dB = RSNI / 2 - 10``; dabei kaemen fuer
    dieselben Verbindungen unplausibel niedrige Werte heraus. Weil der Detektor
    ohnehin gegen das Ruheverhalten jeder Strecke normiert, faellt eine
    abweichende Skalierung nicht ins Gewicht - entscheidend ist allein, dass
    der Wert mit der Bewegung schwankt.
    """
    for key in _SNR_KEYS:
        value = _num(link.get(key))
        if value is None or value >= _NICHT_VERFUEGBAR:
            continue
        if 0 <= value <= 100:
            return value
    return None


def _first_int(link: dict, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _num(link.get(key))
        if value is not None:
            return int(value)
    return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
