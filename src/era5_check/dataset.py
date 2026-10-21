"""Paired hourly series per station: IMGW measurement next to the reanalyses at the station point.

Two frames per station, both on a regular UTC grid over the study period:
    wind   index = observation time; obs [m/s, whole m/s], obs_dir, and per reanalysis (``config.MODELS``) its
           instantaneous 10 m speed (column = model name) and direction (``<model>_dir``)
    solar  index = start of the hour; obs and per reanalysis GHI [W/m2, hourly mean], temp [deg C] and wind [m/s]
           (measured, hourly means of the two bounding observations), zenith [deg] at mid-hour

Radiation quality control on the solar-time records, before they are moved to UTC: a value is dropped when it
exceeds the BSRN "physically possible" limit 1.5 S0 cos(z)^1.2 + 100 W/m2, or when it is zero with the sun
more than 10 degrees above the horizon (a recorder outage, not a dark sky).

Usage:
    wind, solar = station_frames(250, load_stations().loc[250], Path("data"))
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib

from era5_check import energy, imgw, openmeteo
from era5_check.config import END, MODELS, START, YEARS

S0 = 1361.0
HOUR = pd.Timedelta("1h")
log = logging.getLogger(__name__)


def utc_grid() -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC") + pd.Timedelta("23h"),
                         freq="h", name="time").as_unit("ns")


def load_synop(code: int, data_dir: Path) -> pd.DataFrame:
    parts = []
    for year in YEARS:
        path = data_dir / "imgw" / "synop" / f"{year}_{code}_s.zip"
        if path.exists():
            parts.append(imgw.parse_synop(path.read_bytes()))
    df = pd.concat(parts)
    return df[~df.index.duplicated()].reindex(utc_grid())


def radiation_qc(rad: pd.DataFrame, lat: float, lon: float) -> pd.DataFrame:
    """NaN for physically impossible or outage values (see module docstring); returns a copy."""
    days = pd.DatetimeIndex(rad["date"])
    offset_h = lon / 15.0 + imgw.equation_of_time_min(days.dayofyear.to_numpy()) / 60.0
    mid_utc = days.tz_localize("UTC") + pd.to_timedelta((rad["label"].to_numpy() - 0.5 - offset_h) * 3600, unit="s")
    sp = pvlib.solarposition.get_solarposition(pd.DatetimeIndex(mid_utc), lat, lon)
    mu = np.cos(np.radians(sp["zenith"].to_numpy())).clip(min=0.0)
    w_m2 = rad["j_cm2"].to_numpy(dtype=float) * imgw.J_CM2_TO_WH_M2
    limit = 1.5 * S0 * mu ** 1.2 + 100.0
    bad = (w_m2 > limit) | ((w_m2 == 0) & (mu > np.sin(np.radians(10.0))))
    out = rad.copy()
    out.loc[bad, "j_cm2"] = np.nan
    return out


def radiation_records(code: int, lat: float, lon: float, data_dir: Path) -> pd.DataFrame:
    """Quality-controlled solar-time records (date, label, j_cm2) of one station."""
    files = sorted(p for p in (data_dir / "imgw" / "akt" / str(code)).rglob("*") if p.suffix.lower() == ".dan")
    return radiation_qc(imgw.parse_dan_files(files), lat, lon)


def load_radiation(code: int, lat: float, lon: float, data_dir: Path, shift_min: float = 0.0,
                   records: pd.DataFrame | None = None) -> pd.Series:
    rad = records if records is not None else radiation_records(code, lat, lon, data_dir)
    return imgw.solar_to_utc_hourly(rad, lon, shift_min).reindex(utc_grid())


def load_reanalysis(code: int, model: str, lat: float, lon: float, data_dir: Path) -> pd.DataFrame:
    df = openmeteo.fetch_point(lat, lon, model, MODELS[model], START, END, data_dir / "openmeteo", key=f"s{code}")
    return df.reindex(utc_grid())


def interval_mean(s: pd.Series) -> pd.Series:
    """Instantaneous values at full hours -> mean over [t, t + 1 h), indexed by t."""
    return (s + s.shift(-1)) / 2.0


def station_frames(code: int, row: pd.Series, data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    syn = load_synop(code, data_dir)
    grid = utc_grid()
    wind = pd.DataFrame({"obs": syn["wind_speed"], "obs_dir": syn["wind_dir"]}, index=grid)
    solar = pd.DataFrame({"obs": load_radiation(code, row["lat"], row["lon"], data_dir),
                          "temp": interval_mean(syn["temp"]), "wind": interval_mean(syn["wind_speed"])}, index=grid)
    for model, variables in MODELS.items():
        re = load_reanalysis(code, model, row["lat"], row["lon"], data_dir)
        wind[model] = re["wind_speed_10m"]
        if "wind_direction_10m" in variables:
            wind[f"{model}_dir"] = re["wind_direction_10m"]
        # Open-Meteo radiation stamped T is the mean of [T - 1 h, T): restamp to the interval start
        solar[model] = re["shortwave_radiation"].shift(-1).clip(lower=0.0)
    geo = energy.solar_geometry(grid, row["lat"], row["lon"], row["elev_m"])
    for col in ("zenith", "apparent_zenith", "azimuth"):
        solar[col] = geo[col].to_numpy()
    return wind, solar
