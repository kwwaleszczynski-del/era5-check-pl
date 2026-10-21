import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from era5_check import imgw


def synop_zip(rows: list[list[str]]) -> bytes:
    """s_t zip with 107 columns; ``rows`` give (year, month, day, hour, dir, dir_st, speed, speed_st, temp, temp_st)."""
    lines = []
    for r in rows:
        cols = ['""'] * 107
        cols[0], cols[1] = '"353180250"', '"TORUŃ"'
        for i, v in zip([2, 3, 4, 5, 23, 24, 25, 26, 29, 30], r):
            cols[i] = v
        lines.append(",".join(cols))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("s_t_250_2012.csv", ("\r\n".join(lines) + "\r\n").encode("cp1250"))
    return buf.getvalue()


def test_parse_synop_status_and_codes():
    raw = synop_zip([
        ['"2012"', '"01"', '"01"', '"00"', "230", '""', "4", '""', "1.5", '""'],
        ['"2012"', '"01"', '"01"', '"01"', "999", '""', "2", '""', "1.0", '""'],   # variable direction
        ['"2012"', '"01"', '"01"', '"02"', "0", '""', "0", '""', ".0", '"8"'],     # calm, no temperature
        ['"2012"', '"01"', '"01"', '"03"', "250", '""', "0", '"8"', "-.3", '""'],  # no wind measurement
    ])
    df = imgw.parse_synop(raw)
    assert list(df.columns) == ["wind_speed", "wind_dir", "temp"]
    assert str(df.index.tz) == "UTC"
    assert df.index[1] - df.index[0] == pd.Timedelta("1h")
    assert df["wind_speed"].tolist()[:3] == [4.0, 2.0, 0.0]
    assert np.isnan(df["wind_speed"].iloc[3])
    assert df["wind_dir"].iloc[0] == 230.0
    assert np.isnan(df["wind_dir"].iloc[1]) and np.isnan(df["wind_dir"].iloc[2])
    assert np.isnan(df["temp"].iloc[2]) and df["temp"].iloc[3] == pytest.approx(-0.3)


MONTH = """TORUN             2015  06  30
  1     0     0     0     1
 1.0
Godzinne wartosci promieniowania calkowitego                    K!
Termostos 770343
Rejestrator  DR02
    04  05  06  07  08  09
01   0   8  17  87 143 191
02   0   3  -1  16  61 130
Godzinne wartosci uslonecznienia (h)                            U
    04  05  06  07  08  09
01 -.1 0.4 0.5 0.6 0.7 0.8
"""

YEAR = """LEBA              2010   1  31
  1     1     1     1     1
Godzinne wartosci promieniowania calkowitego                    K!
     9  10  11  12  13  14
 1   3   8   9   9  12  10
LEBA              2010   2  28
  1     1     1     1     1
Godzinne wartosci promieniowania rozproszonego                  D
     9  10  11  12  13  14
 1   1   1   1   1   1   1
Godzinne wartosci promieniowania calkowitego                    K!
     9  10  11  12  13  14
 1   5   6   7   8   9  10
 2   1   2   3   4   5   6
"""


def test_parse_dan_month_skips_other_blocks_and_negative():
    df = imgw.parse_dan(MONTH)
    assert len(df) == 12
    assert df["date"].min() == pd.Timestamp("2015-06-01")
    assert df["label"].tolist()[:6] == [4, 5, 6, 7, 8, 9]
    assert df["j_cm2"].iloc[3] == 87.0
    assert df["j_cm2"].isna().sum() == 1            # the -1


def test_parse_dan_year_of_sections():
    df = imgw.parse_dan(YEAR)
    assert sorted(df["date"].dt.month.unique()) == [1, 2]
    feb = df[df["date"].dt.month == 2]
    assert feb["j_cm2"].tolist() == [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]  # not diffuse


def test_parse_dan_without_radiation_block():
    with pytest.raises(ValueError):
        imgw.parse_dan("TORUN 2015 06 30\nGodzinne wartosci uslonecznienia (h) U\n 4 5 6 7 8 9\n")


def long_day(values: dict[int, float], date="2012-06-21") -> pd.DataFrame:
    return pd.DataFrame({"date": pd.Timestamp(date), "label": list(values), "j_cm2": list(values.values())})


def test_solar_to_utc_keeps_daily_energy_and_shifts_by_longitude():
    rad = long_day({11: 100.0, 12: 200.0, 13: 300.0, 14: 0.0})
    out = imgw.solar_to_utc_hourly(rad, lon=15.0)        # TST = UTC + 1 h + equation of time
    total_wh = out.sum()
    assert total_wh == pytest.approx(600.0 * imgw.J_CM2_TO_WH_M2)
    e = imgw.equation_of_time_min(np.array([173]))[0] / 60.0
    assert -0.1 < e < 0                                  # sun slightly slow on 21 June
    # solar hour 12-13 (label 13) starts at UTC 11 + |e|: a share 1 - |e| of it lands in the UTC hour 11-12,
    # which also receives the last |e| of the solar hour 11-12 (label 12)
    peak = out.idxmax()
    assert peak.hour == 11
    assert out.loc[peak] == pytest.approx((300.0 * (1 + e) + 200.0 * -e) * imgw.J_CM2_TO_WH_M2)
    night = out[(out.index.hour < 5) & (out.index.normalize() == pd.Timestamp("2012-06-21", tz="UTC"))]
    assert (night == 0).all()


def test_solar_to_utc_missing_hour_poisons_neighbours_only():
    rad = long_day({11: 100.0, 12: np.nan, 13: 300.0, 14: 50.0, 15: 10.0})
    out = imgw.solar_to_utc_hourly(rad, lon=22.5)        # offset 1.5 h: every solar hour spans two UTC hours
    day = out[out.index.normalize() == pd.Timestamp("2012-06-21", tz="UTC")]
    assert len(day) == 24 and day.isna().sum() == 2        # hours outside the covered day are NaN as well
    assert 60.0 * imgw.J_CM2_TO_WH_M2 < day.dropna().sum() < 460.0 * imgw.J_CM2_TO_WH_M2


def test_equation_of_time_range():
    e = imgw.equation_of_time_min(np.arange(1, 366))
    assert e.min() == pytest.approx(-14.6, abs=1.0)
    assert e.max() == pytest.approx(16.4, abs=1.0)
