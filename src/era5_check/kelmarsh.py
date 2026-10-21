"""Optional hub-height check: ERA5 wind at 78.5 m against the nacelle anemometers of the Kelmarsh wind farm (UK).

Measured: 10-minute nacelle wind speed of the four 78.5 m hub turbines, rebuilt as the mean of the two
anemometers where both exist (the SCADA "Wind speed" signal changes definition in April 2018, see
wind-power-curve-kelmarsh), averaged over the hour centred on each full hour and over the turbines (at least
three). Timestamps of the 10-minute records are the start of the period.
Model: ERA5 10 m and 100 m wind interpolated to the hub with the hourly power-law exponent
alpha = ln(v100 / v10) / ln(10), clipped to [0, 0.45].

The data come from the Parquet cache of https://github.com/kwwaleszczynski-del/wind-power-curve-kelmarsh
(``processed/scada_YYYY.parquet``), pointed to by the environment variable ``KELMARSH_DATA_DIR``; source:
Plumley C., Takeuchi R., Kelmarsh wind farm data, doi:10.5281/zenodo.16807551, CC BY 4.0.

Usage:
    pair = hub_height_pairs(Path(os.environ["KELMARSH_DATA_DIR"]), era5_frame)   # obs, era5 at hub [m/s]
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from era5_check.config import KELMARSH

SHEAR_CLIP = (0.0, 0.45)


def nacelle_hourly(data_dir: Path) -> pd.Series:
    """Farm-mean nacelle wind speed per full hour (UTC) from the processed SCADA Parquet files."""
    parts = []
    for year in range(KELMARSH["start"].year, KELMARSH["end"].year + 1):
        path = data_dir / "processed" / f"scada_{year}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=["ws", "ws1", "ws2"]).reset_index()
        df = df[df["turbine"].isin(KELMARSH["turbines"])]
        ws = df[["ws1", "ws2"]].mean(axis=1, skipna=False).fillna(df["ws"])
        mid = pd.DatetimeIndex(df["timestamp"]).as_unit("ns") + pd.Timedelta("5min")
        hour = (mid + pd.Timedelta("30min")).floor("h")            # records centred within +-30 min
        parts.append(pd.DataFrame({"turbine": df["turbine"].to_numpy(), "hour": hour, "ws": ws.to_numpy()}))
    if not parts:
        raise FileNotFoundError(f"no Kelmarsh SCADA Parquet files in {data_dir / 'processed'}")
    df = pd.concat(parts, ignore_index=True).dropna()
    per_turbine = df.groupby(["hour", "turbine"])["ws"].agg(["mean", "count"])
    per_turbine = per_turbine[per_turbine["count"] >= 5]["mean"].unstack()
    farm = per_turbine.mean(axis=1).where(per_turbine.notna().sum(axis=1) >= 3)
    farm.index = pd.DatetimeIndex(farm.index, name="time").as_unit("ns")
    return farm.rename("obs")


def era5_at_hub(era5: pd.DataFrame, hub_m: float = KELMARSH["hub_m"]) -> pd.Series:
    v10, v100 = era5["wind_speed_10m"].to_numpy(dtype=float), era5["wind_speed_100m"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = np.log(v100 / v10) / np.log(10.0)
    alpha = np.where((v10 > 0.5) & (v100 > 0.5) & np.isfinite(alpha), alpha, 1.0 / 7.0)
    return pd.Series(v100 * (hub_m / 100.0) ** np.clip(alpha, *SHEAR_CLIP), index=era5.index, name="era5")


def hub_height_pairs(data_dir: Path, era5: pd.DataFrame) -> pd.DataFrame:
    obs = nacelle_hourly(data_dir)
    model = era5_at_hub(era5)
    return pd.concat([obs, model, era5["wind_speed_100m"].rename("era5_100m")], axis=1, join="inner").dropna()
