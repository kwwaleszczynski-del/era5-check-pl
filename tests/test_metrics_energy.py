import numpy as np
import pandas as pd
import pytest

from era5_check import energy, metrics


def test_scores_known_values():
    obs = np.array([1.0, 2.0, 3.0, 4.0] * 5)
    s = metrics.scores(obs, obs * 2.0)
    assert s["n"] == 20
    assert s["bias"] == pytest.approx(2.5)
    assert s["rel_bias_pct"] == pytest.approx(100.0)
    assert s["r2"] == pytest.approx(1.0)
    assert s["std_ratio"] == pytest.approx(2.0)
    assert s["rmse"] == pytest.approx(np.sqrt(np.mean(obs ** 2)))


def test_scores_ignore_nan_and_small_samples():
    obs = np.r_[np.arange(12.0), np.nan]
    s = metrics.scores(obs, np.r_[np.arange(12.0) + 1.0, 5.0])
    assert s["n"] == 12 and s["bias"] == pytest.approx(1.0)
    assert np.isnan(metrics.scores([1.0, 2.0], [1.0, 2.0])["bias"])


def test_grouped_scores_by_label():
    obs = np.tile(np.arange(10.0), 4)
    lab = np.repeat(["a", "b"], 20)
    mod = obs + np.where(lab == "a", 1.0, -1.0)
    g = metrics.grouped_scores(obs, mod, lab)
    assert g.loc["a", "bias"] == pytest.approx(1.0) and g.loc["b", "bias"] == pytest.approx(-1.0)


def test_weibull_fit_recovers_parameters():
    rng = np.random.default_rng(1)
    v = 6.0 * rng.weibull(2.2, 50_000)
    k, a = metrics.weibull_fit(v)
    assert k == pytest.approx(2.2, rel=0.03) and a == pytest.approx(6.0, rel=0.02)


def test_seasons():
    idx = pd.date_range("2012-01-15", periods=12, freq="30D", tz="UTC")
    assert list(metrics.season_of(idx)[:3]) == ["DJF", "DJF", "MAM"]


def test_power_curve_shape():
    p = energy.power_pu([0.0, 2.0, 8.0, 15.0, 24.0, 26.0, np.nan])
    assert p[0] == 0.0 and p[1] < 0.02
    assert 0.2 < p[2] < 0.7
    assert p[3] == pytest.approx(1.0, abs=0.02) and p[4] == 1.0
    assert p[5] == 0.0 and np.isnan(p[6])


def test_quantize_half_up():
    assert energy.quantize([0.49, 0.5, 2.5, 3.2]).tolist() == [0.0, 1.0, 3.0, 3.0]


def test_wind_energy_error_signs():
    rng = np.random.default_rng(2)
    true = 3.5 * rng.weibull(2.0, 50_000)
    obs = energy.quantize(true)                            # the station reports whole m/s
    same = energy.wind_energy_error(obs, true)
    assert abs(same["energy_error_pct"]) < 1.0             # rounding the model like the station: like for like
    assert same["k"] * obs.mean() == pytest.approx(energy.HUB_MEAN_MS)
    high = energy.wind_energy_error(obs, true * 1.1)
    assert high["speed_error_pct"] == pytest.approx(10.0, abs=0.5)
    assert high["energy_error_pct"] > 10.0                 # the curve amplifies a speed error at 7 m/s


def test_capacity_factor_weights():
    assert energy.capacity_factor([5.0, 15.0], [1.0, 0.0]) == pytest.approx(energy.power_pu([5.0])[0])


def hours(day="2012-06-21"):
    return pd.date_range(f"{day} 00:00", periods=24, freq="h", tz="UTC")


def test_pv_yield_day_night_and_nan():
    idx = hours()
    geo = energy.solar_geometry(idx, 52.0, 19.0, 100.0)
    clear = (900.0 * np.cos(np.radians(geo["zenith"])).clip(lower=0.0)).rename("ghi")
    temp = pd.Series(20.0, index=idx)
    wind = pd.Series(2.0, index=idx)
    y = energy.pv_yield(clear, temp, wind, 52.0, 19.0, 100.0)
    assert y.iloc[0] == 0.0 and y.iloc[10] > 0.5
    assert y.sum() == pytest.approx(7.0, rel=0.3)             # kWh/kWp on a clear June day
    half = energy.pv_yield(clear * 0.5, temp, wind, 52.0, 19.0, 100.0)
    assert 0.4 < half.sum() / y.sum() < 0.6
    gap = clear.copy()
    gap.iloc[10] = np.nan
    assert np.isnan(energy.pv_yield(gap, temp, wind, 52.0, 19.0, 100.0).iloc[10])
