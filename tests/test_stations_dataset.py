import numpy as np
import pandas as pd
import pytest

from era5_check import dataset, stations


def test_station_table():
    st = stations.load_stations()
    assert len(st) == 19 and st.index.is_unique
    assert set(st["terrain"]) == {"coast", "lowland", "upland", "summit"}
    assert (st.loc[st["terrain"] == "coast", "sea_km"] < 5).all()
    assert (st.loc[st["terrain"] != "coast", "sea_km"] > 100).all()
    assert st["lat"].between(49, 55).all() and st["lon"].between(14, 24.2).all()


def test_distance_to_polyline():
    line = [(50.0, 10.0), (50.0, 12.0)]
    assert stations.distance_to_polyline_km(51.0, 11.0, line) == pytest.approx(111.2, rel=0.01)
    assert stations.distance_to_polyline_km(50.0, 13.0, line) == pytest.approx(71.5, rel=0.02)


def test_radiation_qc_drops_outage_and_impossible_values():
    rad = pd.DataFrame({"date": pd.Timestamp("2012-06-21"), "label": [1, 12, 13, 14],
                        "j_cm2": [0.0, 0.0, 300.0, 900.0]})
    out = dataset.radiation_qc(rad, 52.0, 19.0)
    assert out["j_cm2"].iloc[0] == 0.0                 # night zero is fine
    assert np.isnan(out["j_cm2"].iloc[1])              # zero at noon: recorder outage
    assert out["j_cm2"].iloc[2] == 300.0
    assert np.isnan(out["j_cm2"].iloc[3])              # 2500 W/m2 is impossible


def test_interval_mean():
    s = pd.Series([0.0, 2.0, 4.0], index=pd.date_range("2012-01-01", periods=3, freq="h", tz="UTC"))
    assert dataset.interval_mean(s).tolist()[:2] == [1.0, 3.0]
