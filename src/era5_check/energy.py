"""From weather error to energy error: wind (MM92 power curve) and PV (pvlib).

Wind. Synoptic stations measure at 10 m, far below a hub. Both the measured and the reanalysis series are
multiplied by the same factor ``k = HUB_MEAN_MS / mean(measured)``, which puts the station's measured resource at
a typical Polish hub-height mean of 7 m/s and keeps the relative speed error and the shape of both distributions.
The capacity factor then comes from the empirical Senvion MM92 curve (Kelmarsh 2017, CC BY 4.0). Because the
stations report whole m/s, series compared with them are rounded to whole m/s first (``quantize``), so both sides
carry the same reporting step.

PV. Hourly GHI -> Erbs decomposition -> Perez transposition to a 30 deg south plane -> physical incidence angle
modifier -> SAPM cell temperature (measured air temperature and wind) -> PVWatts DC and inverter (DC/AC 1.2).
The measured and the reanalysis GHI run through the same chain with the same temperature and wind, so the
difference in yield is due to irradiance alone.

Usage:
    k = hub_scale(obs_10m)
    cf = capacity_factor(quantize(obs_10m) * k)
    pv = pv_yield(ghi, temp_air, wind_10m, lat=53.0, lon=18.6, elevation_m=69)   # kWh/kWp per hour
"""
from __future__ import annotations

from importlib import resources

import numpy as np
import pandas as pd
import pvlib

HUB_MEAN_MS = 7.0
RATED_KW = 2050.0
CUT_OUT_MS = 25.0
CURVE_FILE = "mm92_kelmarsh_2017.csv"
HALF_HOUR = pd.Timedelta("30min")
TEMP_PARAMS = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"]
PV = {"tilt": 30.0, "azimuth": 180.0, "albedo": 0.2, "dc_ac": 1.2, "gamma_pdc": -0.0035, "dc_losses": 0.08,
      "inverter_eff": 0.98}


def load_power_curve() -> tuple[np.ndarray, np.ndarray]:
    """Wind speed [m/s] and power as a fraction of rated, from the packaged empirical MM92 curve."""
    with resources.files("era5_check").joinpath("data", CURVE_FILE).open("r", encoding="utf-8") as f:
        df = pd.read_csv(f, comment="#")
    return df["ws_ms"].to_numpy(dtype=float), (df["power_kw"] / RATED_KW).clip(0.0, 1.0).to_numpy(dtype=float)


_CURVE = load_power_curve()


def power_pu(ws) -> np.ndarray:
    """Power as a fraction of rated; zero above cut-out; NaN speeds stay NaN."""
    ws = np.asarray(ws, dtype=float)
    p = np.interp(ws, *_CURVE, left=0.0, right=0.0)
    p[ws > CUT_OUT_MS] = 0.0
    p[np.isnan(ws)] = np.nan
    return p


def quantize(ws) -> np.ndarray:
    """Round to whole m/s, the reporting step of the synoptic wind speed (half-up)."""
    return np.floor(np.asarray(ws, dtype=float) + 0.5)


def hub_scale(obs, hub_mean: float = HUB_MEAN_MS) -> float:
    o = np.asarray(obs, dtype=float)
    return float(hub_mean / np.nanmean(o))


def capacity_factor(ws, weights=None) -> float:
    """Mean of the power curve over the speeds (optionally weighted), NaNs ignored."""
    p = power_pu(ws)
    w = np.ones_like(p) if weights is None else np.asarray(weights, dtype=float)
    ok = np.isfinite(p) & np.isfinite(w)
    return float((p[ok] * w[ok]).sum() / w[ok].sum())


def wind_energy_error(obs, model, hub_mean: float = HUB_MEAN_MS) -> dict:
    """Capacity factors on the pairs where both exist, with the station's hub scaling, and the relative errors."""
    o = np.asarray(obs, dtype=float)
    m = np.asarray(model, dtype=float)
    ok = np.isfinite(o) & np.isfinite(m)
    o, m = o[ok], m[ok]
    k = hub_scale(o, hub_mean)
    cf_obs = capacity_factor(o * k)
    cf_mod = capacity_factor(quantize(m) * k)
    speed_err = (m.mean() - o.mean()) / o.mean()
    energy_err = cf_mod / cf_obs - 1.0
    return {"k": k, "cf_obs": cf_obs, "cf_model": cf_mod, "speed_error_pct": 100.0 * speed_err,
            "energy_error_pct": 100.0 * energy_err,
            "elasticity": energy_err / speed_err if abs(speed_err) >= 0.05 else np.nan}


def solar_geometry(index: pd.DatetimeIndex, lat: float, lon: float, elevation_m: float) -> pd.DataFrame:
    """Solar position at the middle of each hourly interval (index = interval start, UTC)."""
    mid = index + HALF_HOUR
    sp = pvlib.solarposition.get_solarposition(mid, lat, lon, altitude=elevation_m)
    sp.index = index
    return sp


def pv_yield(ghi: pd.Series, temp_air: pd.Series, wind: pd.Series, lat: float, lon: float, elevation_m: float,
             geometry: pd.DataFrame | None = None) -> pd.Series:
    """AC energy [kWh/kWp] of each hourly interval (index = interval start, UTC); NaN where an input is NaN."""
    sp = geometry if geometry is not None else solar_geometry(ghi.index, lat, lon, elevation_m)
    zen = sp["apparent_zenith"].to_numpy()
    azi = sp["azimuth"].to_numpy()
    g = ghi.to_numpy(dtype=float).clip(min=0.0)
    mid = ghi.index + HALF_HOUR
    split = pvlib.irradiance.erbs(g, sp["zenith"].to_numpy(), mid.dayofyear.to_numpy())
    dni = np.nan_to_num(np.asarray(split["dni"], dtype=float), nan=0.0).clip(min=0.0)
    dhi = np.asarray(split["dhi"], dtype=float)
    dni_extra = pvlib.irradiance.get_extra_radiation(mid).to_numpy()
    airmass = pvlib.atmosphere.get_relative_airmass(zen)
    poa = pvlib.irradiance.get_total_irradiance(PV["tilt"], PV["azimuth"], zen, azi, dni, g, dhi,
                                                dni_extra=dni_extra, airmass=airmass, albedo=PV["albedo"],
                                                model="perez")
    aoi = pvlib.irradiance.aoi(PV["tilt"], PV["azimuth"], zen, azi)
    poa_eff = np.asarray(poa["poa_direct"]) * pvlib.iam.physical(aoi) + np.asarray(poa["poa_diffuse"])
    poa_eff = np.where(zen < 90.0, np.nan_to_num(poa_eff, nan=0.0), 0.0).clip(min=0.0)
    t_cell = pvlib.temperature.sapm_cell(poa_eff, temp_air.to_numpy(dtype=float), wind.to_numpy(dtype=float),
                                         **TEMP_PARAMS)
    pdc = pvlib.pvsystem.pvwatts_dc(poa_eff, t_cell, 1.0, PV["gamma_pdc"]) * (1.0 - PV["dc_losses"])
    ac_rating = 1.0 / PV["dc_ac"]
    pac = pvlib.inverter.pvwatts(np.asarray(pdc, dtype=float), ac_rating / PV["inverter_eff"],
                                 eta_inv_nom=PV["inverter_eff"])
    pac = np.clip(np.asarray(pac, dtype=float), 0.0, ac_rating)
    bad = np.isnan(ghi.to_numpy(dtype=float)) | np.isnan(np.asarray(t_cell, dtype=float))
    pac[bad] = np.nan
    return pd.Series(pac, index=ghi.index, name="pv_kwh_kwp")
