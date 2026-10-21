"""IMGW-PIB public data: hourly synoptic observations and hourly global radiation.

Sources (https://danepubliczne.imgw.pl, "Zrodlem pochodzenia danych jest Instytut Meteorologii i Gospodarki
Wodnej - Panstwowy Instytut Badawczy"):
    dane_meteorologiczne/terminowe/synop/{year}/{year}_{code}_s.zip   one CSV per station and year (s_t format)
    dane_aktynometryczne/{station}/Zweryfikowane/{year}/*.DAN           verified hourly global radiation, J/cm2

Conventions after parsing:
    synop  index = UTC observation time; ``wind_speed`` [m/s] is the 10-minute mean before the hour, reported
           in whole m/s; ``wind_dir`` [deg] (NaN for calm or variable); ``temp`` [deg C].
    radiation  hours in the DAN files are true solar time, value stamped at the END of the hour (11-12 is
           stamped 12, the IMGW convention for sunshine until 2015). ``solar_to_utc_hourly`` moves the energy
           onto UTC hours, keeping the daily total, and returns the mean irradiance [W/m2] of each UTC hour,
           indexed by the hour START.

Usage:
    zips = [fetch_synop(250, y, Path("data/imgw")) for y in range(2010, 2016)]
    obs = pd.concat([parse_synop(z.read_bytes()) for z in zips])
    files = fetch_actinometric("Toruń", 250, range(2010, 2016), Path("data/imgw"))
    rad = parse_dan_files(files)                         # long frame: day, hour label, J/cm2
    ghi = solar_to_utc_hourly(rad, lon=18.6)
"""
from __future__ import annotations

import io
import logging
import re
import time
import urllib.parse
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/"
SYNOP_URL = BASE + "dane_meteorologiczne/terminowe/synop/{year}/{year}_{code}_s.zip"
AKT_URL = BASE + "dane_aktynometryczne/"
J_CM2_TO_WH_M2 = 1e4 / 3600.0          # 1 J/cm2 over one hour = 2.778 W/m2 mean
# s_t columns used (0-based, see s_t_format.txt): year, month, day, hour, KRWR + status, FWR + status, TEMP + status
SYNOP_COLS = {"year": 2, "month": 3, "day": 4, "hour": 5, "wind_dir": 23, "wind_dir_st": 24,
              "wind_speed": 25, "wind_speed_st": 26, "temp": 29, "temp_st": 30}
log = logging.getLogger(__name__)


def _get(url: str, session: requests.Session, tries: int = 3) -> requests.Response:
    for i in range(tries):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 404:
                return r
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(5.0 * (i + 1))
    raise AssertionError("unreachable")


def list_dir(url: str, session: requests.Session) -> list[str]:
    """Entries of an Apache directory index (sub-folders keep their trailing '/')."""
    r = _get(url, session)
    if r.status_code == 404:
        return []
    names = re.findall(r'<a href="([^"?/][^"]*)"', r.text)
    return [urllib.parse.unquote(n) for n in names]


def fetch_synop(code: int, year: int, cache_dir: Path, session: requests.Session | None = None) -> Path | None:
    """Zip of one station-year of hourly synoptic data (cached); None when IMGW has no file."""
    path = cache_dir / "synop" / f"{year}_{code}_s.zip"
    if path.exists():
        return path
    session = session or requests.Session()
    r = _get(SYNOP_URL.format(year=year, code=code), session)
    if r.status_code == 404:
        log.warning("IMGW synop %s %s: no file", code, year)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    log.info("IMGW synop %s %s: %.0f kB", code, year, len(r.content) / 1024)
    return path


def fetch_actinometric(folder: str, code: int, years, cache_dir: Path,
                       session: requests.Session | None = None) -> list[Path]:
    """Verified hourly radiation files of one station for the given years (cached per year)."""
    session = session or requests.Session()
    root = cache_dir / "akt" / str(code)
    out: list[Path] = []
    station_url = AKT_URL + urllib.parse.quote(folder) + "/"
    verified = None
    for year in years:
        ydir = root / str(year)
        done = ydir / ".complete"
        if not done.exists():
            if verified is None:
                top = list_dir(station_url, session)
                verified = next((n for n in top if n.lower().startswith("zweryfikowane")), "")
                avail = set(list_dir(station_url + urllib.parse.quote(verified), session)) if verified else set()
            ydir.mkdir(parents=True, exist_ok=True)
            if f"{year}/" in avail:
                year_url = station_url + urllib.parse.quote(verified) + f"{year}/"
                for name in list_dir(year_url, session):
                    if name.lower().endswith(".dan") and not (ydir / name).exists():
                        (ydir / name).write_bytes(_get(year_url + urllib.parse.quote(name), session).content)
                        time.sleep(0.1)
            done.write_text("ok")
            log.info("IMGW radiation %s %s: %d files", folder, year, len(list(ydir.glob("*.[dD][aA][nN]"))))
        out.extend(sorted(p for p in ydir.iterdir() if p.suffix.lower() == ".dan"))
    return out


def parse_synop(zip_bytes: bytes) -> pd.DataFrame:
    """One s_t zip -> hourly frame indexed by UTC time with wind_speed, wind_dir, temp (NaN when missing)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        raw = z.read(z.namelist()[0]).decode("cp1250")
    df = pd.read_csv(io.StringIO(raw), header=None, usecols=list(SYNOP_COLS.values()), dtype=str,
                     keep_default_na=False)
    df.columns = list(SYNOP_COLS)
    stamp = pd.to_datetime(dict(year=df["year"].astype(int), month=df["month"].astype(int),
                                day=df["day"].astype(int), hour=df["hour"].astype(int)), utc=True)
    out = pd.DataFrame(index=pd.DatetimeIndex(stamp, name="time").as_unit("ns"))
    for col in ("wind_speed", "wind_dir", "temp"):
        val = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float, copy=True)
        status = df[f"{col}_st"].str.strip().to_numpy()
        val[status == "8"] = np.nan                         # "8" = no measurement
        out[col] = val
    calm = out["wind_speed"] == 0
    out.loc[calm | (out["wind_dir"] > 360), "wind_dir"] = np.nan   # 999 = variable direction
    return out[~out.index.duplicated()].sort_index()


_HEAD = re.compile(r"^\s*[A-Za-z][A-Za-z .\-]*?\s+(\d{4})\s+(\d{1,2})\s+(\d{1,2})\s*$")
_TITLE = re.compile(r"promieniowani[ae]\s+ca[lł]kowit", re.I)
_LABELS = re.compile(r"\s*(\d{1,2}\s+){5,}\d{1,2}\s*")
_ROW = re.compile(r"^\s*(\d{1,2})((?:\s+-?\d*\.?\d+)+)\s*$")


def parse_dan(text: str) -> pd.DataFrame:
    """A DAN file (one month, or a year of monthly sections) -> long frame (date, label, j_cm2).

    Each monthly section starts with ``NAME  YYYY  MM  DD`` and holds one or more blocks; the global radiation
    block is titled "Godzinne wartosci promieniowania calkowitego", followed by a row of hour labels and one row
    per day. Other blocks (diffuse, reflected, balance, sunshine) are skipped. Values below zero are missing.
    """
    lines = text.splitlines()
    rows = []
    year = month = None
    i = 0
    while i < len(lines):
        head = _HEAD.match(lines[i])
        if head:
            year, month = int(head.group(1)), int(head.group(2))
            i += 1
            continue
        if year is None or not _TITLE.search(lines[i]):
            i += 1
            continue
        i += 1
        while i < len(lines) and not _LABELS.fullmatch(lines[i]) and not _HEAD.match(lines[i]):
            i += 1
        if i >= len(lines) or _HEAD.match(lines[i]):
            continue
        labels = [int(x) for x in lines[i].split()]
        i += 1
        while i < len(lines):
            m = _ROW.match(lines[i])
            if not m:
                break
            vals = [float(x) for x in m.group(2).split()]
            if len(vals) == len(labels):
                rows.extend((year, month, int(m.group(1)), h, v) for h, v in zip(labels, vals))
            i += 1
        year = None                                   # one radiation block per monthly section
    if not rows:
        raise ValueError("no global radiation block")
    df = pd.DataFrame(rows, columns=["year", "month", "day", "label", "j_cm2"])
    df["date"] = pd.to_datetime(df[["year", "month", "day"]], errors="coerce")
    df = df.dropna(subset=["date"])
    df.loc[df["j_cm2"] < 0, "j_cm2"] = np.nan
    return df[["date", "label", "j_cm2"]].reset_index(drop=True)


def parse_dan_files(paths: list[Path]) -> pd.DataFrame:
    """All DAN files of a station -> one long frame; later files win on duplicate (date, label)."""
    parts = []
    for p in paths:
        try:
            parts.append(parse_dan(p.read_bytes().decode("cp1250", errors="replace")))
        except ValueError as exc:
            log.warning("%s: %s", p.name, exc)
    if not parts:
        return pd.DataFrame(columns=["date", "label", "j_cm2"])
    df = pd.concat(parts, ignore_index=True)
    return df.drop_duplicates(["date", "label"], keep="last").sort_values(["date", "label"]).reset_index(drop=True)


def equation_of_time_min(dayofyear: np.ndarray) -> np.ndarray:
    """Equation of time [min] (Spencer 1971), true solar time minus mean solar time."""
    b = 2.0 * np.pi * (np.asarray(dayofyear, dtype=float) - 1.0) / 365.0
    return 229.18 * (0.000075 + 0.001868 * np.cos(b) - 0.032077 * np.sin(b)
                     - 0.014615 * np.cos(2 * b) - 0.040849 * np.sin(2 * b))


def solar_to_utc_hourly(rad: pd.DataFrame, lon: float, shift_min: float = 0.0) -> pd.Series:
    """Hour-ending true-solar-time energies -> mean irradiance [W/m2] of each UTC hour (index = hour start).

    Each solar hour [label - 1, label) is moved to UTC by ``lon / 15 h + equation of time`` (plus an optional
    ``shift_min`` used to test the timing) and its energy is split over the two UTC hours it overlaps, in
    proportion to the overlap. Hours of a day that are not in the file are night (zero) when the day has any
    value; a UTC hour that receives energy from a missing solar hour is NaN.
    """
    if rad.empty:
        return pd.Series(dtype=float, name="ghi")
    days = pd.DatetimeIndex(rad["date"])
    offset_h = lon / 15.0 + equation_of_time_min(days.dayofyear.to_numpy()) / 60.0 + shift_min / 60.0
    start_h = rad["label"].to_numpy(dtype=float) - 1.0 - offset_h           # UTC hours after local midnight
    j0 = np.floor(start_h)
    w0 = j0 + 1.0 - start_h                                                  # share in hour j0
    day_ns = days.as_unit("ns").asi8
    hour_ns = 3600 * 10**9
    e = rad["j_cm2"].to_numpy(dtype=float)
    miss = np.isnan(e)
    idx = np.concatenate([day_ns + (j0 * hour_ns).astype(np.int64), day_ns + ((j0 + 1) * hour_ns).astype(np.int64)])
    val = np.concatenate([np.where(miss, 0.0, e * w0), np.where(miss, 0.0, e * (1.0 - w0))])
    bad = np.concatenate([miss & (w0 > 0), miss & (w0 < 1)])
    part = pd.DataFrame({"t": idx, "e": val, "bad": bad}).groupby("t").agg(e=("e", "sum"), bad=("bad", "any"))
    # every hour of the covered days, night hours included, so that missing days stay missing
    first, last = days.min().as_unit("ns").value, days.max().as_unit("ns").value + 24 * hour_ns
    full = np.arange(first - 3 * hour_ns, last + 3 * hour_ns, hour_ns, dtype=np.int64)
    covered = np.isin((full // (24 * hour_ns)) * (24 * hour_ns), np.unique(day_ns))
    out = part.reindex(full)
    e_full = out["e"].to_numpy(dtype=float)
    e_full = np.where(np.isnan(e_full) & covered, 0.0, e_full)
    e_full[out["bad"].eq(True).to_numpy()] = np.nan
    index = pd.DatetimeIndex(pd.to_datetime(full, utc=True), name="time").as_unit("ns")
    return pd.Series(e_full * J_CM2_TO_WH_M2, index=index, name="ghi")
