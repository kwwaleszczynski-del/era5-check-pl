"""Static interactive dashboard (Plotly.js) in ``docs/index.html``: station map and per-station charts.

The page is one self-contained HTML file: summary tables and per-station aggregates are embedded as JSON and
drawn by Plotly.js from the CDN, so it works on GitHub Pages without a server.

Usage:
    payload = station_payload(wind, solar, ["era5"])          # per station, during validation
    write_dashboard(tables, payloads, Path("docs/index.html"))
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd

from era5_check.validate import daily_sums

MODEL_BIN = 0.5


def station_payload(wind: pd.DataFrame, solar: pd.DataFrame, models: list[str]) -> dict:
    """Compact aggregates for one station: 2-D wind histograms and daily GHI pairs."""
    out = {}
    daily = daily_sums(solar, ["obs"] + models)
    for model in models:
        w = wind[["obs", model]].dropna()
        x_edges = np.arange(-0.5, max(w["obs"].max(), 1) + 1.5, 1.0)
        y_edges = np.arange(0, max(w[model].max(), 1) + MODEL_BIN, MODEL_BIN)
        h, _, _ = np.histogram2d(w["obs"], w[model], [x_edges, y_edges])
        d = daily[["obs", model]].dropna()
        out[model] = {"wind_obs": (x_edges[:-1] + 0.5).tolist(), "wind_model": (y_edges[:-1] + MODEL_BIN / 2).tolist(),
                      "wind_counts": h.astype(int).T.tolist(),
                      "ghi_day": d.index.strftime("%Y-%m-%d").tolist(),
                      "ghi_obs": d["obs"].round(2).tolist(), "ghi_model": d[model].round(2).tolist()}
    return out


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = []
    for r in df[cols].to_dict("records"):
        out.append({k: (None if isinstance(v, float) and not np.isfinite(v) else
                        round(v, 4) if isinstance(v, float) else v.item() if hasattr(v, "item") else v)
                    for k, v in r.items()})
    return out


def write_dashboard(tables: dict[str, pd.DataFrame], payloads: dict, summary: dict, path: Path) -> Path:
    """Render ``docs/index.html`` from the result tables, the per-station payloads and the headline numbers."""
    base_cols = ["code", "name", "terrain", "elev_m", "sea_km", "lat", "lon", "wind_night_coverage", "pv_years"]
    model_cols = ["wind_speed_error_pct", "wind_energy_error_pct", "ghi_error_pct", "pv_error_pct", "pv_obs_kwh_kwp",
                  "pv_model_kwh_kwp", "wind_cf_obs", "wind_cf_model", "weibull_k_obs", "weibull_k_model"]
    stations: dict[int, dict] = {}
    for model in summary["models"]:
        e = tables["energy"][tables["energy"]["model"] == model]
        sc = tables["scores"][tables["scores"]["model"] == model]
        wide = sc.pivot(index="code", columns="sample", values=["rel_bias_pct", "rmse", "r2", "bias", "n"])
        wide.columns = [f"{m}_{s}" for m, s in wide.columns]
        for rec in _records(e.merge(wide.reset_index(), on="code"), base_cols + model_cols + list(wide.columns)):
            st = stations.setdefault(rec["code"], {k: rec[k] for k in base_cols} | {"m": {}})
            st["m"][model] = {k: v for k, v in rec.items() if k not in base_cols}
    m = tables["mcp"][tables["mcp"]["scheme"] == "leave_one_year_out"]
    data = {
        "stations": list(stations.values()),
        "cycle": _records(tables["cycle"], ["code", "model", "sample", "hour_utc", "bias"]),
        "season": _records(tables["season"], ["code", "model", "sample", "month", "rel_bias_pct"]),
        "dist": _records(tables["wind_dist"], ["code", "model", "speed", "freq_obs", "freq_model"]),
        "mcp": _records(m, ["code", "test", "method", "energy_error_pct", "speed_error_pct", "wasserstein_ms"]),
        "payload": {str(k): v for k, v in payloads.items()},
        "summary": summary,
    }
    template = resources.files("era5_check").joinpath("data", "dashboard_template.html").read_text(encoding="utf-8")
    html = template.replace("__DATA__", json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
