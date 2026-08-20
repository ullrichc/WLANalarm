from wlanalarm.model import LinkSample, make_link_id, normalise_mac


def test_normalise_mac_vereinheitlicht_schreibweisen():
    assert normalise_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalise_mac("AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF"
    assert normalise_mac("") == ""


def test_link_id_ist_richtungsunabhaengig():
    a = make_link_id("AA:BB:CC:DD:EE:01", "11:22:33:44:55:66", "5 GHz")
    b = make_link_id("11:22:33:44:55:66", "AA:BB:CC:DD:EE:01", "5 GHz")
    assert a == b


def test_link_id_trennt_baender():
    """Dasselbe Geraet auf zwei Baendern sind zwei unabhaengige Sensoren."""
    assert make_link_id("AA:BB:CC:DD:EE:01", "11:22:33:44:55:66", "5 GHz") != make_link_id(
        "AA:BB:CC:DD:EE:01", "11:22:33:44:55:66", "6 GHz"
    )


def test_sample_serialisierung_ist_verlustfrei():
    sample = LinkSample(
        ts=1.5, link_id="x", ap_mac="AA:BB:CC:DD:EE:01", ap_name="Box",
        peer_mac="11:22:33:44:55:66", peer_name="TV", band="5 GHz",
        rssi_dbm=-55.5, rx_rate_kbps=866000, mcs=9, streams=2,
    )
    assert LinkSample.from_dict(sample.to_dict()) == sample


def test_from_dict_toleriert_unbrauchbare_werte():
    sample = LinkSample.from_dict(
        {"ts": 1.0, "link_id": "x", "rssi_dbm": "keine Zahl", "mcs": ""}
    )
    assert sample.rssi_dbm is None
    assert sample.mcs is None
