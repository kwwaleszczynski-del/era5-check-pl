"""Measure-correlate-predict (MCP): long-term correction of a short measurement with a reanalysis reference.

Reference x = reanalysis wind speed (and direction) at the station, target y = measured wind speed.
Three transfer functions are fitted on concurrent training years and applied to the reference of the test years:

    linear      y = a + b x, ordinary least squares. Unbiased in the mean but shrinks the spread by r.
    variance    y = mean_y + (std_y / std_x)(x - mean_x), the variance ratio method: keeps mean and spread.
    matrix      empirical distribution of y in each (reference speed bin, direction sector) cell; the predicted
                distribution is the mixture of the cells weighted by how often the test reference visits them.
                Cells with fewer than ``MIN_CELL`` samples fall back to the speed bin over all sectors, then to
                the variance ratio prediction.

``evaluate`` compares predicted and measured test-year distributions: mean speed error, energy error with the
MM92 power curve (``energy.wind_energy_error`` conventions: station hub scaling, predictions rounded to whole m/s
like the synoptic report) and the Wasserstein (earth mover's) distance between the distributions.

Usage:
    folds = leave_one_year_out(years)                    # [(train_years, test_years), ...]
    table = evaluate(x, direction, y, year, folds)       # one row per fold and method
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from era5_check import energy

SPEED_BIN = 1.0
SECTORS = 12
MIN_CELL = 10
METHODS = ("raw", "linear", "variance", "matrix")


def fit_linear(x, y) -> tuple[float, float]:
    b, a = np.polyfit(np.asarray(x, dtype=float), np.asarray(y, dtype=float), 1)
    return float(a), float(b)


def fit_variance_ratio(x, y) -> tuple[float, float]:
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    b = y.std() / x.std()
    return float(y.mean() - b * x.mean()), float(b)


def affine(x, a: float, b: float) -> np.ndarray:
    return np.clip(a + b * np.asarray(x, dtype=float), 0.0, None)


def _cells(x, d) -> tuple[np.ndarray, np.ndarray]:
    sbin = np.floor(np.asarray(x, dtype=float) / SPEED_BIN).astype(int)
    sector = (np.floor((np.asarray(d, dtype=float) % 360.0) / (360.0 / SECTORS) + 0.5).astype(int)) % SECTORS
    return sbin, sector


class MatrixMCP:
    """Conditional distribution of the target in (reference speed bin, direction sector) cells."""

    def fit(self, x, d, y) -> "MatrixMCP":
        sbin, sector = _cells(x, d)
        y = np.asarray(y, dtype=float)
        df = pd.DataFrame({"s": sbin, "c": sector, "y": y})
        self.cell = {k: g.to_numpy() for k, g in df.groupby(["s", "c"])["y"] if len(g) >= MIN_CELL}
        self.speed = {k: g.to_numpy() for k, g in df.groupby("s")["y"] if len(g) >= MIN_CELL}
        self.fallback = fit_variance_ratio(x, y)
        return self

    def predict_distribution(self, x, d) -> tuple[np.ndarray, np.ndarray]:
        """Values and weights of the predicted distribution (weights sum to the number of test samples)."""
        sbin, sector = _cells(x, d)
        x = np.asarray(x, dtype=float)
        counts = pd.DataFrame({"s": sbin, "c": sector}).value_counts()
        values, weights, rest = [], [], []
        for (s, c), n in counts.items():
            pool = self.cell.get((s, c))
            if pool is None:
                pool = self.speed.get(s)
            if pool is None:
                rest.append((s, c))
                continue
            values.append(pool)
            weights.append(np.full(len(pool), n / len(pool)))
        if rest:
            mask = pd.MultiIndex.from_arrays([sbin, sector]).isin(rest)
            values.append(affine(x[mask], *self.fallback))
            weights.append(np.ones(int(mask.sum())))
        return np.concatenate(values), np.concatenate(weights)

    def predict_point(self, x, d) -> np.ndarray:
        """Mean of the cell for each reference value (a time series, for hourly scores)."""
        sbin, sector = _cells(x, d)
        out = affine(x, *self.fallback)
        for i, key in enumerate(zip(sbin, sector)):
            pool = self.cell.get(key)
            if pool is None:
                pool = self.speed.get(key[0])
            if pool is not None:
                out[i] = pool.mean()
        return out


def leave_one_year_out(years) -> list[tuple[list[int], list[int]]]:
    ys = sorted(set(int(y) for y in years))
    return [([t for t in ys if t != y], [y]) for y in ys]


def one_year_campaign(years) -> list[tuple[list[int], list[int]]]:
    ys = sorted(set(int(y) for y in years))
    return [([y], [t for t in ys if t != y]) for y in ys]


def _summary(pred_values, pred_weights, y_test, k: float) -> dict:
    mean_pred = float(np.average(pred_values, weights=pred_weights))
    cf_pred = energy.capacity_factor(pred_values * k, pred_weights)
    cf_obs = energy.capacity_factor(y_test * k)
    return {"speed_error_pct": 100.0 * (mean_pred / y_test.mean() - 1.0),
            "energy_error_pct": 100.0 * (cf_pred / cf_obs - 1.0),
            "wasserstein_ms": float(stats.wasserstein_distance(pred_values, y_test, u_weights=pred_weights))}


def evaluate(x, d, y, year, folds, hub_mean: float = energy.HUB_MEAN_MS) -> pd.DataFrame:
    """One row per (fold, method) with the test-year errors of the predicted distribution."""
    x, d, y, year = (np.asarray(v, dtype=float) for v in (x, d, y, year))
    ok = np.isfinite(x) & np.isfinite(d) & np.isfinite(y)
    x, d, y, year = x[ok], d[ok], y[ok], year[ok]
    k = energy.hub_scale(y, hub_mean)
    rows = []
    for train_years, test_years in folds:
        tr, te = np.isin(year, train_years), np.isin(year, test_years)
        if tr.sum() < 100 or te.sum() < 100:
            continue
        xt, yt = x[te], y[te]
        lin = fit_linear(x[tr], y[tr])
        var = fit_variance_ratio(x[tr], y[tr])
        mat = MatrixMCP().fit(x[tr], d[tr], y[tr])
        preds = {
            "raw": (energy.quantize(xt), np.ones(len(xt))),
            "linear": (energy.quantize(affine(xt, *lin)), np.ones(len(xt))),
            "variance": (energy.quantize(affine(xt, *var)), np.ones(len(xt))),
            "matrix": mat.predict_distribution(xt, d[te]),
        }
        points = {"raw": xt, "linear": affine(xt, *lin), "variance": affine(xt, *var),
                  "matrix": mat.predict_point(xt, d[te])}
        for method in METHODS:
            vals, w = preds[method]
            rows.append({"train": ",".join(map(str, train_years)), "test": ",".join(map(str, test_years)),
                         "method": method, "n_train": int(tr.sum()), "n_test": int(te.sum()),
                         "rmse_ms": float(np.sqrt(np.mean((points[method] - yt) ** 2)))}
                        | _summary(vals, w, yt, k))
    return pd.DataFrame(rows)
