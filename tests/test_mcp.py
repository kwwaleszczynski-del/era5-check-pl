import numpy as np
import pytest

from era5_check import mcp


def synthetic(n_years=4, seed=3):
    """Reference x (Weibull, hourly), direction, and a target 0.7 x + noise, rounded to whole m/s."""
    rng = np.random.default_rng(seed)
    n = 8760 * n_years
    x = 4.0 * rng.weibull(2.0, n)
    d = rng.uniform(0, 360, n)
    speedup = np.where((d > 180) & (d < 300), 0.9, 0.6)          # direction-dependent site
    y = np.clip(np.round(speedup * x + rng.normal(0, 0.6, n)), 0, None)
    year = np.repeat(np.arange(2010, 2010 + n_years), 8760)
    return x, d, y, year


def test_fit_linear_and_variance_ratio():
    rng = np.random.default_rng(4)
    x = rng.normal(8, 1.5, 10_000)
    y = 1.0 + 0.5 * x + rng.normal(0, 1, 10_000)
    a, b = mcp.fit_linear(x, y)
    assert a == pytest.approx(1.0, abs=0.05) and b == pytest.approx(0.5, abs=0.01)
    a, b = mcp.fit_variance_ratio(x, y)
    pred = mcp.affine(x, a, b)
    assert pred.std() == pytest.approx(y.std(), rel=1e-3) and pred.mean() == pytest.approx(y.mean(), abs=0.02)
    assert (mcp.affine([-10.0], 0.0, 1.0) == 0.0).all()


def test_matrix_distribution_weights_and_direction():
    x, d, y, _ = synthetic(2)
    m = mcp.MatrixMCP().fit(x, d, y)
    values, weights = m.predict_distribution(x[:5000], d[:5000])
    assert weights.sum() == pytest.approx(5000)
    assert np.average(values, weights=weights) == pytest.approx(y[:5000].mean(), rel=0.03)
    point = m.predict_point(np.array([6.0, 6.0]), np.array([240.0, 60.0]))
    assert point[0] > point[1]                                    # learned the direction speed-up


def test_folds():
    assert mcp.leave_one_year_out([2011, 2010, 2011]) == [([2011], [2010]), ([2010], [2011])]
    assert mcp.one_year_campaign([2010, 2011, 2012])[0] == ([2010], [2011, 2012])


def test_evaluate_corrections_beat_raw_and_linear_shrinks_spread():
    x, d, y, year = synthetic(4)
    t = mcp.evaluate(x, d, y, year, mcp.leave_one_year_out(year))
    assert set(t["method"]) == set(mcp.METHODS) and len(t) == 4 * 4
    err = t.groupby("method")["energy_error_pct"].apply(lambda s: s.abs().mean())
    assert err["raw"] > 20.0
    assert err[["variance", "matrix"]].max() < 5.0
    wd = t.groupby("method")["wasserstein_ms"].mean()
    assert wd["matrix"] < wd["linear"] and wd["variance"] < wd["linear"]
