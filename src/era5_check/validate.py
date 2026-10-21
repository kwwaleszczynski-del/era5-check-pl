"""Per-station validation tables: error scores, daily cycle, seasons, energy impact, MCP.

Samples:
    wind        every hour where both the station and the reanalysis have a value (10 m, m/s)
    ghi_hourly  hours with the sun above 5 degrees at mid-hour (W/m2)
    ghi_daily   days with all 24 UTC hours present (kWh/m2/day)

Usage:
    tables = station_tables(code, row, wind, solar, models=["era5"])   # dict of DataFrames
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from era5_check import energy, mcp, metrics

DAY_ZENITH = 85.0
NIGHT_HOURS = (22, 23, 0, 1, 2, 3)          # UTC; a station without them reports in daytime only
MET_GAP_H = 12
WIND_BINS = np.arange(0, 26, 1.0)


def daily_sums(solar: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """kWh/m2 per UTC day; NaN for a day with any missing hour."""
    g = solar[cols].groupby(solar.index.floor("D"))
    day = g.sum().where(g.count() == 24)
    return day / 1000.0


def scores_row(code: int, model: str, sample: str, obs, mod) -> dict:
    return {"code": code, "model": model, "sample": sample} | metrics.scores(obs, mod)


def station_tables(code: int, row: pd.Series, wind: pd.DataFrame, solar: pd.DataFrame,
                   models: list[str]) -> dict[str, pd.DataFrame]:
    scores, cycle, season, energy_rows, dist = [], [], [], [], []
    day_mask = solar["apparent_zenith"] < DAY_ZENITH
    daily = daily_sums(solar, ["obs"] + models)
    for model in models:
        w = wind[["obs", model]].dropna()
        s = solar.loc[day_mask, ["obs", model]].dropna()
        d = daily[["obs", model]].dropna()
        scores += [scores_row(code, model, "wind", w["obs"], w[model]),
                   scores_row(code, model, "ghi_hourly", s["obs"], s[model]),
                   scores_row(code, model, "ghi_daily", d["obs"], d[model])]
        for sample, frame, hours in (("wind", w, w.index.hour), ("ghi_hourly", s, s.index.hour)):
            g = metrics.grouped_scores(frame["obs"], frame[model], hours)
            cycle.append(g.assign(code=code, model=model, sample=sample).rename_axis("hour_utc").reset_index())
        for sample, frame in (("wind", w), ("ghi_daily", d)):
            g = metrics.grouped_scores(frame["obs"], frame[model], frame.index.month)
            season.append(g.assign(code=code, model=model, sample=sample).rename_axis("month").reset_index())
        k_obs, a_obs = metrics.weibull_fit(w["obs"])
        k_mod, a_mod = metrics.weibull_fit(energy.quantize(w[model]))
        wind_e = energy.wind_energy_error(w["obs"], w[model])
        pv = pv_error(row, solar, model)
        night = np.isin(wind.index.hour, NIGHT_HOURS)
        night_cov = float(wind.loc[night, ["obs", model]].notna().all(axis=1).mean())
        energy_rows.append({"code": code, "model": model, "wind_night_coverage": night_cov,
                            "weibull_k_obs": k_obs, "weibull_a_obs": a_obs,
                            "weibull_k_model": k_mod, "weibull_a_model": a_mod}
                           | {f"wind_{k}": v for k, v in wind_e.items()} | pv)
        h_obs = np.histogram(w["obs"], WIND_BINS - 0.5)[0]
        h_mod = np.histogram(energy.quantize(w[model]), WIND_BINS - 0.5)[0]
        dist.append(pd.DataFrame({"code": code, "model": model, "speed": WIND_BINS[:-1],
                                  "freq_obs": h_obs / h_obs.sum(), "freq_model": h_mod / h_mod.sum()}))
    return {"scores": pd.DataFrame(scores), "cycle": pd.concat(cycle, ignore_index=True),
            "season": pd.concat(season, ignore_index=True), "energy": pd.DataFrame(energy_rows),
            "wind_dist": pd.concat(dist, ignore_index=True)}


def pv_error(row: pd.Series, solar: pd.DataFrame, model: str) -> dict:
    """Specific PV yield [kWh/kWp/yr] from measured and reanalysis GHI, on complete days.

    A day counts when both GHI series are present for all 24 hours and the measured temperature and wind are
    present whenever the sun is up, after filling gaps of up to ``MET_GAP_H`` hours by linear interpolation
    (a station without night reports misses the early-morning hours of summer days).
    """
    met = solar[["temp", "wind"]].interpolate(limit=MET_GAP_H, limit_direction="both", limit_area="inside")
    sun_up = solar["zenith"] < 90.0
    met_ok = met.notna().all(axis=1) | ~sun_up
    ok = solar[["obs", model]].notna().all(axis=1) & met_ok
    full = ok.groupby(solar.index.floor("D")).transform("all")
    sub = solar[full].copy()
    sub[["temp", "wind"]] = met[full].fillna(0.0)
    geo = sub[["zenith", "apparent_zenith", "azimuth"]]
    args = (sub["temp"], sub["wind"], row["lat"], row["lon"], row["elev_m"])
    y_obs = energy.pv_yield(sub["obs"], *args, geometry=geo)
    y_mod = energy.pv_yield(sub[model], *args, geometry=geo)
    years = full.sum() / 24.0 / 365.25
    ghi_obs, ghi_mod = sub["obs"].sum() / 1000.0 / years, sub[model].sum() / 1000.0 / years
    return {"pv_years": float(years), "ghi_obs_kwh_m2": ghi_obs, "ghi_model_kwh_m2": ghi_mod,
            "ghi_error_pct": 100.0 * (ghi_mod / ghi_obs - 1.0),
            "pv_obs_kwh_kwp": float(y_obs.sum() / years), "pv_model_kwh_kwp": float(y_mod.sum() / years),
            "pv_error_pct": 100.0 * float(y_mod.sum() / y_obs.sum() - 1.0)}


def station_mcp(code: int, wind: pd.DataFrame, reference: str = "era5") -> pd.DataFrame:
    """MCP folds with ``reference`` speed and the ERA5 direction: leave-one-year-out and one-year campaigns."""
    w = wind[["obs", reference, "era5_dir"]].dropna()
    year = w.index.year.to_numpy()
    out = []
    for scheme, folds in (("leave_one_year_out", mcp.leave_one_year_out(year)),
                          ("one_year_campaign", mcp.one_year_campaign(year))):
        t = mcp.evaluate(w[reference], w["era5_dir"], w["obs"], year, folds)
        out.append(t.assign(code=code, reference=reference, scheme=scheme))
    return pd.concat(out, ignore_index=True)
