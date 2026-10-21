"""End-to-end steps: fetch -> validate -> energy -> mcp -> report.

Every step reads the cache written by ``fetch`` (``data/``) and writes tables to ``results/``; ``report`` turns the
tables into PNG charts (``docs/img``) and the interactive dashboard (``docs/index.html``).

Usage:
    fetch(Path("data"))
    tables = validate(Path("data"), Path("results"))
    report(Path("data"), Path("results"), Path("docs"))
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from era5_check import dashboard, dataset, energy, imgw, kelmarsh, metrics, openmeteo, plots
from era5_check import validate as validate_mod
from era5_check.config import END, KELMARSH, MODELS, START, YEARS
from era5_check.stations import load_stations

log = logging.getLogger(__name__)


def kelmarsh_dir() -> Path:
    """Folder with the Kelmarsh SCADA cache of wind-power-curve-kelmarsh (``processed/scada_YYYY.parquet``)."""
    default = Path(__file__).resolve().parents[3] / "wind-power-curve-kelmarsh" / "data"
    return Path(os.environ.get("KELMARSH_DATA_DIR", default))


def fetch(data_dir: Path, max_weight: float = 2000.0, refresh: bool = False, with_kelmarsh: bool = True) -> None:
    """Download IMGW station data and ERA5 / ERA5-Land point series into ``data_dir`` (cached)."""
    st = load_stations()
    session = requests.Session()
    session.headers["User-Agent"] = "era5-check-pl (research script)"
    for code, row in st.iterrows():
        for year in YEARS:
            imgw.fetch_synop(int(code), year, data_dir / "imgw", session)
        imgw.fetch_actinometric(row["akt_folder"], int(code), YEARS, data_dir / "imgw", session)
    om = requests.Session()
    for code, row in st.iterrows():
        for model, variables in MODELS.items():
            openmeteo.fetch_point(row["lat"], row["lon"], model, variables, START, END, data_dir / "openmeteo",
                                  key=f"s{code}", refresh=refresh, max_daily_weight=max_weight, session=om)
    if with_kelmarsh:
        k = KELMARSH
        openmeteo.fetch_point(k["lat"], k["lon"], "era5", k["variables"], k["start"], k["end"],
                              data_dir / "openmeteo", key="kelmarsh", refresh=refresh,
                              max_daily_weight=max_weight, session=om)


def timing_check(code: int, row: pd.Series, solar: pd.DataFrame, data_dir: Path,
                 shifts=range(-60, 61, 15)) -> pd.DataFrame:
    """Correlation of measured and ERA5 hourly GHI when the solar-time records are shifted by ``shifts`` minutes.

    A wrong hour convention would put the best match at +-60 min; the analysis itself always uses shift 0.
    """
    ref = solar["era5"]
    records = dataset.radiation_records(code, row["lat"], row["lon"], data_dir)
    rows = []
    for s in shifts:
        obs = dataset.load_radiation(code, row["lat"], row["lon"], data_dir, shift_min=s, records=records)
        ok = obs.notna() & ref.notna() & ((obs > 0) | (ref > 0))
        rows.append({"code": code, "shift_min": s, "r": float(np.corrcoef(obs[ok], ref[ok])[0, 1])})
    return pd.DataFrame(rows)


def validate(data_dir: Path, results_dir: Path) -> dict[str, pd.DataFrame]:
    """All station tables (CSV in ``results_dir``) and the per-station dashboard payload (``data_dir/derived``)."""
    st = load_stations()
    models = list(MODELS)
    tables: dict[str, list[pd.DataFrame]] = {}
    payload = {}
    for code, row in st.iterrows():
        code = int(code)
        wind, solar = dataset.station_frames(code, row, data_dir)
        t = validate_mod.station_tables(code, row, wind, solar, models)
        t["mcp"] = validate_mod.station_mcp(code, wind, reference="era5")
        t["timing"] = timing_check(code, row, solar, data_dir)
        for name, df in t.items():
            tables.setdefault(name, []).append(df)
        payload[code] = dashboard.station_payload(wind, solar, models)
        e = t["energy"].iloc[0]
        log.info("%s: wind bias %+.0f %% -> energy %+.0f %%, GHI %+.1f %% -> PV %+.1f %%", row["name"],
                 e["wind_speed_error_pct"], e["wind_energy_error_pct"], e["ghi_error_pct"], e["pv_error_pct"])
    out = {name: pd.concat(parts, ignore_index=True) for name, parts in tables.items()}
    meta = st.reset_index()[["code", "name", "terrain", "elev_m", "sea_km", "lat", "lon"]]
    out["scores"] = meta.merge(out["scores"], on="code")
    out["energy"] = meta.merge(out["energy"], on="code")
    k = kelmarsh_table(data_dir)
    if k is not None:
        out["kelmarsh"], out["kelmarsh_distribution"] = k
    results_dir.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        df.to_csv(results_dir / f"{name}.csv", index=False, float_format="%.4g")
    derived = data_dir / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "dashboard_stations.json").write_text(json.dumps(payload, separators=(",", ":")))
    return out


def kelmarsh_table(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Hub-height scores with energy error at Kelmarsh and the two speed distributions, or None without data."""
    k = KELMARSH
    try:
        era5 = openmeteo.fetch_point(k["lat"], k["lon"], "era5", k["variables"], k["start"], k["end"],
                                     data_dir / "openmeteo", key="kelmarsh")
        pair = kelmarsh.hub_height_pairs(kelmarsh_dir(), era5)
    except (FileNotFoundError, RuntimeError) as exc:
        log.warning("Kelmarsh hub-height check skipped: %s", exc)
        return None
    rows = []
    for col, label in (("era5", "ERA5 interpolated to 78.5 m"), ("era5_100m", "ERA5 100 m as is")):
        s = metrics.scores(pair["obs"], pair[col])
        cf_obs = energy.capacity_factor(pair["obs"])
        cf_mod = energy.capacity_factor(pair[col])
        rows.append({"series": label} | s | {"cf_obs": cf_obs, "cf_model": cf_mod,
                                             "energy_error_pct": 100.0 * (cf_mod / cf_obs - 1.0)})
    edges = np.arange(0.0, 26.0, 1.0)
    dist = pd.DataFrame({"speed": edges[:-1],
                         "obs": 100.0 * np.histogram(pair["obs"], edges)[0] / len(pair),
                         "era5": 100.0 * np.histogram(pair["era5"], edges)[0] / len(pair)})
    return pd.DataFrame(rows), dist


TABLES = ("scores", "cycle", "season", "energy", "wind_dist", "mcp", "timing", "kelmarsh", "kelmarsh_distribution")


def mcp_summary(mcp_t: pd.DataFrame) -> pd.DataFrame:
    """Per (scheme, method): mean absolute and mean signed errors over stations and folds."""
    g = mcp_t.assign(abs_energy_error_pct=mcp_t["energy_error_pct"].abs(),
                     abs_speed_error_pct=mcp_t["speed_error_pct"].abs(),
                     within_5pct=mcp_t["energy_error_pct"].abs() <= 5.0)
    return g.groupby(["scheme", "method"]).agg(
        abs_energy_error_pct=("abs_energy_error_pct", "mean"), energy_error_pct=("energy_error_pct", "mean"),
        abs_speed_error_pct=("abs_speed_error_pct", "mean"), wasserstein_ms=("wasserstein_ms", "mean"),
        rmse_ms=("rmse_ms", "mean"), share_within_5pct=("within_5pct", "mean"), folds=("code", "size"))


def headline(tables: dict[str, pd.DataFrame], mcp_s: pd.DataFrame) -> dict:
    e = tables["energy"][tables["energy"]["model"] == "era5"]
    full = e[e["wind_night_coverage"] >= 0.5]
    flat = full[full["terrain"].isin(["coast", "lowland"])]
    summit = e[e["terrain"] == "summit"]
    loyo = mcp_s.loc["leave_one_year_out"].drop(index="raw")
    best = loyo["abs_energy_error_pct"].idxmin()
    label = {"linear": "Linear regression", "variance": "Variance ratio", "matrix": "Matrix method"}
    pct = lambda v: f"{v:+.0f} %"
    return {
        "wind_bias_range": f"{pct(flat['wind_speed_error_pct'].min())} to {pct(flat['wind_speed_error_pct'].max())}",
        "summit_bias": " / ".join(pct(v) for v in summit["wind_speed_error_pct"]),
        "ghi_bias_median": f"{e['ghi_error_pct'].median():+.1f} %",
        "ghi_bias_range": f"{e['ghi_error_pct'].min():+.1f} to {e['ghi_error_pct'].max():+.1f} %",
        "best_mcp": label[best], "best_mcp_error": f"{loyo.loc[best, 'abs_energy_error_pct']:.1f} %",
        "default_station": 250,
    }


def report(data_dir: Path, results_dir: Path, docs_dir: Path) -> dict:
    """PNG charts, dashboard and ``results/summary.json`` from the validation tables."""
    tables = {n: pd.read_csv(results_dir / f"{n}.csv") for n in TABLES if (results_dir / f"{n}.csv").exists()}
    payloads = json.loads((data_dir / "derived" / "dashboard_stations.json").read_text())
    mcp_s = mcp_summary(tables["mcp"])
    mcp_s.reset_index().to_csv(results_dir / "mcp_summary.csv", index=False, float_format="%.4g")
    summary = headline(tables, mcp_s)
    e = tables["energy"][tables["energy"]["model"] == "era5"]
    img = docs_dir / "img"
    plots.bias_map(e, img / "bias_map.png")
    plots.bias_vs_terrain(e, img / "bias_terrain.png")
    plots.daily_cycle(tables["cycle"], tables["season"], e, img / "daily_cycle.png")
    plots.energy_impact(e, img / "energy_impact.png")
    plots.mcp_summary(mcp_s, tables["mcp"]["code"].nunique(), img / "mcp.png")
    plots.example_station(payloads["250"], "Toruń", img / "example_torun.png")
    if "kelmarsh" in tables:
        plots.kelmarsh_figure(tables["kelmarsh_distribution"], tables["kelmarsh"], img / "kelmarsh.png")
    dashboard.write_dashboard(tables, payloads, summary, docs_dir / "index.html")
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
