import pytest

from wlanalarm.recorder import Recorder
from wlanalarm.sources.base import SourceError
from wlanalarm.sources.replay import ReplaySource, iter_recording
from wlanalarm.sources.synthetic import SyntheticSource

from conftest import quiet_links


def test_aufzeichnung_und_wiedergabe_sind_verlustfrei(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    original = [source.scan() for _ in range(10)]

    with Recorder(tmp_path) as recorder:
        for scan in original:
            recorder.write(scan)

    datei = next(tmp_path.glob("samples-*.ndjson"))
    wiedergegeben = list(iter_recording(datei))

    assert len(wiedergegeben) == len(original)
    assert wiedergegeben[0].ts == original[0].ts
    assert wiedergegeben[3].samples == original[3].samples


def test_komprimierte_aufzeichnung(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    with Recorder(tmp_path, compress=True) as recorder:
        for _ in range(5):
            recorder.write(source.scan())
    datei = next(tmp_path.glob("samples-*.ndjson.gz"))
    assert len(list(iter_recording(datei))) == 5


def test_replay_source_meldet_das_ende(tmp_path):
    source = SyntheticSource(quiet_links(), interval=2.0, seed=3)
    with Recorder(tmp_path) as recorder:
        recorder.write(source.scan())
    datei = next(tmp_path.glob("samples-*.ndjson"))

    replay = ReplaySource(datei)
    assert len(replay.scan().samples) == 3
    with pytest.raises(SourceError, match="zu Ende"):
        replay.scan()


def test_fehlende_datei_wird_gemeldet():
    with pytest.raises(SourceError, match="nicht gefunden"):
        ReplaySource("/gibt/es/nicht.ndjson")


def test_kaputte_zeile_wird_gemeldet(tmp_path):
    datei = tmp_path / "kaputt.ndjson"
    datei.write_text('{"ts": 1.0, "samples": []}\nkein json\n', encoding="utf-8")
    with pytest.raises(SourceError, match=":2:"):
        list(iter_recording(datei))


def test_alte_aufzeichnungen_werden_geloescht(tmp_path):
    import os
    import time

    alt = tmp_path / "samples-2020-01-01.ndjson"
    alt.write_text("", encoding="utf-8")
    veraltet = time.time() - 10 * 86400
    os.utime(alt, (veraltet, veraltet))

    recorder = Recorder(tmp_path, retention_days=3)
    assert recorder.purge() == 1
    assert not alt.exists()
    recorder.close()
