"""Agreement between a reanalysis series and station measurements.

The scores follow the validation chapter of the author's thesis: bias = mean(model - obs), relative bias against
the observed mean, RMSE, MAE and R2 as the squared Pearson correlation; added here are the ratio of standard
deviations and a Weibull fit for wind speed distributions.

Usage:
    s = scores(obs, model)                     # dict: n, obs_mean, model_mean, bias, rel_bias_pct, rmse, mae, r2, std_ratio
    by_hour = grouped_scores(obs, model, obs.index.hour)
    k, a = weibull_fit(obs)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

SEASONS = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
           6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
MIN_SAMPLES = 10


def scores(obs, model) -> dict:
    """Scores on the pairs where both values are finite; NaN scores when fewer than MIN_SAMPLES pairs."""
    o = np.asarray(obs, dtype=float)
    m = np.asarray(model, dtype=float)
    ok = np.isfinite(o) & np.isfinite(m)
    o, m = o[ok], m[ok]
    out = {"n": int(ok.sum())}
    if len(o) < MIN_SAMPLES:
        return out | {k: np.nan for k in ("obs_mean", "model_mean", "bias", "rel_bias_pct", "rmse", "mae",
                                           "r2", "std_ratio")}
    d = m - o
    obs_mean = float(o.mean())
    r = stats.pearsonr(o, m)[0] if o.std() > 0 and m.std() > 0 else np.nan
    return out | {
        "obs_mean": obs_mean,
        "model_mean": float(m.mean()),
        "bias": float(d.mean()),
        "rel_bias_pct": float(100.0 * d.mean() / obs_mean) if obs_mean != 0 else np.nan,
        "rmse": float(np.sqrt((d ** 2).mean())),
        "mae": float(np.abs(d).mean()),
        "r2": float(r ** 2),
        "std_ratio": float(m.std() / o.std()) if o.std() > 0 else np.nan,
    }


def grouped_scores(obs: pd.Series, model: pd.Series, by) -> pd.DataFrame:
    """``scores`` per group (``by`` aligned with ``obs``: an array of labels or a Series)."""
    df = pd.DataFrame({"obs": np.asarray(obs, dtype=float), "model": np.asarray(model, dtype=float),
                       "g": np.asarray(by)})
    rows = {g: scores(part["obs"], part["model"]) for g, part in df.groupby("g", sort=True)}
    return pd.DataFrame.from_dict(rows, orient="index")


def season_of(index: pd.DatetimeIndex) -> np.ndarray:
    return np.array([SEASONS[m] for m in index.month])


def weibull_fit(speed) -> tuple[float, float]:
    """Weibull shape k and scale A [m/s] by maximum likelihood (location fixed at 0, calms dropped)."""
    v = np.asarray(speed, dtype=float)
    v = v[np.isfinite(v) & (v > 0)]
    if len(v) < MIN_SAMPLES:
        return np.nan, np.nan
    k, _, a = stats.weibull_min.fit(v, floc=0)
    return float(k), float(a)
