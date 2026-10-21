"""End-to-end check of the table, chart and dashboard steps on synthetic stations (no network, no IMGW files)."""
import json

import numpy as np
import pandas as pd
import pytest

from era5_check import dashboard, energy, pipeline, plots, validate
from era5_check.stations import load_stations


def synthetic_station(row: pd.Series, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2012-01-01", "2013-12-31 23:00", freq="h", tz="UTC")
    n = len(idx)
    era5 = 4.0 * rng.weibull(2.0, n)
    wind = pd.DataFrame({"obs": np.clip(np.round(0.8 * era5 + rng.normal(0, 0.7, n)), 0, None),
                         "obs_dir": rng.uniform(0, 360, n), "era5": era5, "era5_dir": rng.uniform(0, 360, n)},
                        index=idx)
    geo = energy.solar_geometry(idx, row["lat"], row["lon"], row["elev_m"])
    clear = 850.0 * np.cos(np.radians(geo["zenith"].to_numpy())).clip(min=0.0)
    cloud = rng.uniform(0.3, 1.0, n)
    solar = pd.DataFrame({"obs": clear * cloud, "era5": clear * np.clip(cloud * 1.03 + rng.normal(0, 0.05, n), 0, 1.2),
                          "temp": 10.0 + 8 * np.sin(np.arange(n) / 8766 * 2 * np.pi), "wind": 3.0}, index=idx)
    for col in ("zenith", "apparent_zenith", "azimuth"):
        solar[col] = geo[col].to_numpy()
    return wind, solar


@pytest.fixture(scope="module")
def tables():
    st = load_stations().loc[[250, 650]]
    parts, payloads = {}, {}
    for i, (code, row) in enumerate(st.iterrows()):
        wind, solar = synthetic_station(row, i)
        t = validate.station_tables(int(code), row, wind, solar, ["era5"])
        t["mcp"] = validate.station_mcp(int(code), wind)
        for k, v in t.items():
            parts.setdefault(k, []).append(v)
        payloads[str(code)] = dashboard.station_payload(wind, solar, ["era5"])
    out = {k: pd.concat(v, ignore_index=True) for k, v in parts.items()}
    meta = st.reset_index()[["code", "name", "terrain", "elev_m", "sea_km", "lat", "lon"]]
    out["scores"] = meta.merge(out["scores"], on="code")
    out["energy"] = meta.merge(out["energy"], on="code")
    return out, payloads


def test_station_tables_content(tables):
    t, _ = tables
    wind = t["scores"][t["scores"]["sample"] == "wind"]
    assert (wind["rel_bias_pct"] > 15).all()                       # obs = 0.8 ERA5: ERA5 about 25 % high
    e = t["energy"]
    assert (e["ghi_error_pct"].between(0, 6)).all() and (e["pv_error_pct"] > 0).all()
    assert e["pv_years"].iloc[0] == pytest.approx(2.0, abs=0.01)
    assert set(t["mcp"]["scheme"]) == {"leave_one_year_out", "one_year_campaign"}
    assert set(t["cycle"]["hour_utc"]) <= set(range(24))


def test_report_outputs(tables, tmp_path):
    t, payloads = tables
    summary = pipeline.headline(t, pipeline.mcp_summary(t["mcp"]))
    assert summary["best_mcp"] in ("Linear regression", "Variance ratio", "Matrix method")
    e = t["energy"]
    for fn, args in ((plots.bias_map, (e,)), (plots.bias_vs_terrain, (e,)), (plots.energy_impact, (e,)),
                     (plots.daily_cycle, (t["cycle"], t["season"], e))):
        assert fn(*args, tmp_path / f"{fn.__name__}.png").stat().st_size > 10_000
    plots.mcp_summary(pipeline.mcp_summary(t["mcp"]), 2, tmp_path / "mcp.png")
    plots.example_station(payloads["250"], "Toruń", tmp_path / "ex.png")
    html = dashboard.write_dashboard(t, payloads, summary, tmp_path / "index.html").read_text(encoding="utf-8")
    assert "__DATA__" not in html and "Państwowy Instytut Badawczy" in html
    data = json.loads(html.split("const D = ", 1)[1].split(";\n", 1)[0])
    assert len(data["stations"]) == 2 and "250" in data["payload"]
    assert data["stations"][0]["r2_wind"] is not None
