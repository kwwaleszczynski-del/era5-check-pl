"""Station metadata and simple geography (distance to the Baltic coast).

The station table (``data/stations.csv``) lists the 19 IMGW-PIB sites that report both hourly synoptic
observations (wind, temperature) and hourly global radiation. ``BALTIC_COAST`` is a hand-digitised polyline of
the open Polish Baltic coast (Swinoujscie to the Vistula Spit, about 5 km accuracy), used only to rank stations
by distance to the sea.

Usage:
    st = load_stations()                 # DataFrame indexed by IMGW station code
    st["sea_km"]                         # distance to the coast [km]
"""
from __future__ import annotations

import io
from importlib import resources

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0
# (lat, lon) from west to east along the open sea coast; the Hel Peninsula is followed out and back.
BALTIC_COAST = [
    (53.917, 14.250), (53.930, 14.450), (54.030, 14.770), (54.060, 14.930), (54.150, 15.290),
    (54.185, 15.570), (54.270, 16.060), (54.430, 16.380), (54.590, 16.860), (54.670, 17.060),
    (54.760, 17.550), (54.830, 17.980), (54.830, 18.310), (54.790, 18.410), (54.700, 18.600),
    (54.600, 18.810), (54.700, 18.600), (54.790, 18.410), (54.600, 18.470), (54.520, 18.550),
    (54.400, 18.670), (54.350, 18.960), (54.380, 19.440), (54.430, 19.620),
]


def load_stations() -> pd.DataFrame:
    """Station table indexed by IMGW code, with ``sea_km`` added."""
    text = resources.files("era5_check").joinpath("data", "stations.csv").read_text(encoding="utf-8")
    st = pd.read_csv(io.StringIO(text), comment="#").set_index("code")
    st["sea_km"] = [distance_to_polyline_km(lat, lon, BALTIC_COAST) for lat, lon in zip(st["lat"], st["lon"])]
    return st


def distance_to_polyline_km(lat: float, lon: float, line: list[tuple[float, float]]) -> float:
    """Shortest distance from a point to a polyline, on a local equirectangular projection [km]."""
    k = np.pi / 180.0 * EARTH_RADIUS_KM
    pts = np.array(line, dtype=float)
    x = (pts[:, 1] - lon) * k * np.cos(np.radians(lat))
    y = (pts[:, 0] - lat) * k
    a, b = np.column_stack([x[:-1], y[:-1]]), np.column_stack([x[1:], y[1:]])
    ab = b - a
    t = np.clip(-(a * ab).sum(axis=1) / np.maximum((ab * ab).sum(axis=1), 1e-12), 0.0, 1.0)
    closest = a + ab * t[:, None]
    return float(np.sqrt((closest ** 2).sum(axis=1)).min())
