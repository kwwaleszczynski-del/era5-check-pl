"""Reanalysis time series (ERA5, CERRA) at a point from the Open-Meteo Historical Weather API (no API key).

Open-Meteo counts one request as ``max(1, n_vars / 10 * max(1, n_days / 14))`` calls per location and allows
600 calls/min, 5,000/h and 10,000/day per IP address on the free tier. Each (point, model, period) is requested
once and cached as Parquet; every request is written to a ledger (``openmeteo_ledger.json`` in the cache
directory) and refused when it would push the last 24 hours of this project above ``max_daily_weight``.
Requests are paced to at most ``PACE_WEIGHT_PER_MIN`` calls per minute. The limit is shared by every project on
the same IP, so HTTP 429 is never retried in a loop: the per-minute and per-hour limits are waited out once (until
the next minute or full hour), the daily limit stops the run with an error to be resumed after the reset.

Time convention of the returned frame (index = UTC timestamp of the value):
    shortwave_radiation   W/m2, mean of the hour ENDING at the timestamp
    wind_speed_*          m/s, instantaneous at the timestamp
    wind_direction_*      degrees, instantaneous

Usage:
    df = fetch_point(54.75, 17.53, "era5", ["wind_speed_10m"], date(2010, 1, 1), date(2015, 12, 31),
                     cache_dir=Path("data/openmeteo"), key="s120")
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URL = "https://archive-api.open-meteo.com/v1/archive"
PACE_WEIGHT_PER_MIN = 250.0
log = logging.getLogger(__name__)


def request_weight(n_vars: int, start: date, end: date) -> float:
    """Open-Meteo's cost of one request for one location, in 'calls'."""
    n_days = (end - start).days + 1
    return max(1.0, n_vars / 10.0 * max(1.0, n_days / 14.0))


def parse_response(payload: dict, variables: list[str]) -> pd.DataFrame:
    """Hourly JSON payload -> frame indexed by UTC timestamps (ns), one float column per variable."""
    hourly = payload["hourly"]
    index = pd.DatetimeIndex(pd.to_datetime(hourly["time"], unit="s", utc=True), name="time").as_unit("ns")
    df = pd.DataFrame({v: pd.to_numeric(pd.Series(hourly[v]), errors="coerce").to_numpy(dtype=float)
                       for v in variables}, index=index)
    df.attrs = {"grid_lat": float(payload.get("latitude", np.nan)),
                "grid_lon": float(payload.get("longitude", np.nan)),
                "elevation": float(payload.get("elevation", np.nan))}
    return df


class Ledger:
    """Weights of past requests with their time, to stay well below the per-IP daily limit."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: list[list] = json.loads(path.read_text()) if path.exists() else []

    def used(self, seconds: float) -> float:
        now = time.time()
        return float(sum(e[1] for e in self.entries if now - e[0] < seconds))

    def add(self, weight: float, label: str) -> None:
        self.entries.append([time.time(), weight, label])
        self.path.write_text(json.dumps(self.entries))


def cache_path(cache_dir: Path, key: str, model: str, start: date, end: date) -> Path:
    return cache_dir / f"{model}_{key}_{start:%Y%m%d}_{end:%Y%m%d}.parquet"


def fetch_point(lat: float, lon: float, model: str, variables: list[str], start: date, end: date,
                cache_dir: Path, key: str, refresh: bool = False, max_daily_weight: float = 2000.0,
                session: requests.Session | None = None) -> pd.DataFrame:
    """Hourly series of ``variables`` from ``model`` (``era5``, ``cerra``) at one point, cached."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, key, model, start, end)
    if path.exists() and not refresh:
        df = pd.read_parquet(path)
        meta = cache_dir / (path.stem + ".json")
        df.attrs = json.loads(meta.read_text()) if meta.exists() else {}
        return df
    ledger = Ledger(cache_dir / "openmeteo_ledger.json")
    weight = request_weight(len(variables), start, end)
    if ledger.used(86400) + weight > max_daily_weight:
        raise RuntimeError(f"Open-Meteo budget: {ledger.used(86400):.0f} used in 24 h + {weight:.0f} requested "
                           f"> {max_daily_weight:.0f}; try later or raise --max-weight")
    params = {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}",
              "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}",
              "hourly": ",".join(variables), "models": model,
              "timezone": "GMT", "timeformat": "unixtime", "wind_speed_unit": "ms"}
    session = session or requests.Session()
    pause = max(0.0, weight + ledger.used(60) - PACE_WEIGHT_PER_MIN) / PACE_WEIGHT_PER_MIN * 60.0
    if pause > 0:
        time.sleep(min(pause, 61.0))
    for attempt in range(2):
        log.info("Open-Meteo %s %s %s..%s %s (weight %.1f)", model, key, start, end, ",".join(variables), weight)
        r = session.get(URL, params=params, timeout=300)
        if r.status_code != 429:
            ledger.add(weight, f"{model} {key} {start}..{end}")
            break
        reason = r.text[:200]
        if attempt == 1 or "aily" in reason:
            raise RuntimeError(f"Open-Meteo rate limit, resume after the reset: {reason}")
        delay = 65.0 if "inute" in reason else 3660.0 - time.time() % 3600.0
        log.warning("Open-Meteo 429 (%s), waiting %.0f s for the reset", reason, delay)
        time.sleep(delay)
    r.raise_for_status()
    payload = r.json()
    df = parse_response(payload, variables)
    df.to_parquet(path)
    (cache_dir / (path.stem + ".json")).write_text(json.dumps(df.attrs))
    return df
