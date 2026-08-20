from wlanalarm.baseline import (
    BaselineSnapshot,
    RollingBaseline,
    mean_abs_diff,
    robust_scale,
    stdev,
)


def test_robuste_streuung_ignoriert_einen_ausreisser():
    ruhig = [10.0] * 20
    mit_ausreisser = ruhig + [500.0]
    assert robust_scale(mit_ausreisser) == 0.0
    # Die klassische Standardabweichung kippt dagegen komplett um.
    assert stdev(mit_ausreisser) > 100


def test_mittlere_aenderung_trennt_zappeln_von_drift():
    zappelig = [0, 3, 0, 3, 0, 3, 0, 3]
    driftend = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3]
    assert mean_abs_diff(zappelig) > mean_abs_diff(driftend)


def test_mittlere_aenderung_bei_zu_wenigen_werten():
    assert mean_abs_diff([1.0]) == 0.0


def test_kalibrierte_baseline_gilt_bis_genug_eigene_werte_da_sind():
    baseline = RollingBaseline(window_seconds=100, min_samples=5)
    baseline.seed(BaselineSnapshot(median=2.0, scale=0.5, samples=100))
    assert baseline.ready
    assert baseline.snapshot().median == 2.0

    for i in range(5):
        baseline.add(float(i), 7.0)
    assert baseline.snapshot().median == 7.0


def test_alte_werte_fallen_aus_dem_fenster():
    baseline = RollingBaseline(window_seconds=10, min_samples=1)
    for i in range(20):
        baseline.add(float(i), float(i))
    assert baseline.count <= 11
    assert baseline.snapshot().median > 10


def test_ohne_werte_und_ohne_kalibrierung_nicht_bereit():
    assert RollingBaseline(window_seconds=10, min_samples=5).ready is False


def test_snapshot_serialisierung():
    snapshot = BaselineSnapshot(median=1.5, scale=0.25, samples=42, updated=99.0)
    assert BaselineSnapshot.from_dict(snapshot.to_dict()) == snapshot
