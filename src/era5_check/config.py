"""Study period, reanalysis variables and the optional hub-height check site."""
from __future__ import annotations

from datetime import date

START, END = date(2010, 1, 1), date(2015, 12, 31)
YEARS = range(START.year, END.year + 1)
# ERA5 (0.25 deg, global) and CERRA (5.5 km, Copernicus regional reanalysis for Europe, on Open-Meteo until
# 30 June 2021). Open-Meteo serves ERA5-Land only for temperature, humidity, snow and soil: wind and radiation
# come back null, so ERA5-Land is not used.
MODELS = {"era5": ["wind_speed_10m", "wind_direction_10m", "shortwave_radiation"],
          "cerra": ["wind_speed_10m", "shortwave_radiation"]}
# Kelmarsh wind farm (UK): 4 of 6 Senvion MM92 turbines have a 78.5 m hub; SCADA 2016-2021, CC BY 4.0
KELMARSH = {"lat": 52.4013, "lon": -0.9431, "hub_m": 78.5, "turbines": (1, 2, 4, 5),
            "start": date(2016, 1, 1), "end": date(2021, 12, 31),
            "variables": ["wind_speed_10m", "wind_speed_100m"]}
