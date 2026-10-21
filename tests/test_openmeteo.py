from datetime import date

import numpy as np
import pandas as pd
import pytest

from era5_check import openmeteo


def payload(n=3, start=1262304000):            # 2010-01-01 00:00 UTC
    return {"latitude": 53.0, "longitude": 18.5, "elevation": 70.0,
            "hourly": {"time": [start + 3600 * i for i in range(n)],
                       "wind_speed_10m": [1.0, None, 3.0][:n], "shortwave_radiation": [0.0, 10.0, 20.0][:n]}}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


def test_request_weight():
    assert openmeteo.request_weight(3, date(2010, 1, 1), date(2015, 12, 31)) == pytest.approx(3 / 10 * 2191 / 14)
    assert openmeteo.request_weight(2, date(2010, 1, 1), date(2010, 1, 2)) == 1.0


def test_parse_response_utc_index_and_nan():
    df = openmeteo.parse_response(payload(), ["wind_speed_10m", "shortwave_radiation"])
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2010-01-01 00:00", tz="UTC")
    assert np.isnan(df["wind_speed_10m"].iloc[1])
    assert df.attrs["grid_lon"] == 18.5


def test_fetch_point_caches_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(openmeteo.time, "sleep", lambda s: None)
    session = FakeSession([FakeResponse(200, payload())])
    args = (53.0, 18.6, "era5", ["wind_speed_10m", "shortwave_radiation"], date(2010, 1, 1), date(2010, 1, 1))
    df = openmeteo.fetch_point(*args, cache_dir=tmp_path, key="s1", session=session)
    again = openmeteo.fetch_point(*args, cache_dir=tmp_path, key="s1", session=FakeSession([]))
    assert len(session.calls) == 1 and session.calls[0]["models"] == "era5"
    pd.testing.assert_frame_equal(df, again, check_index_type=False)
    assert again.attrs["grid_lat"] == 53.0
    assert len(openmeteo.Ledger(tmp_path / "openmeteo_ledger.json").entries) == 1


def test_fetch_point_retries_after_429(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(openmeteo.time, "sleep", slept.append)
    session = FakeSession([FakeResponse(429, {"reason": "Minutely API request limit exceeded"}),
                           FakeResponse(200, payload())])
    openmeteo.fetch_point(53.0, 18.6, "era5", ["wind_speed_10m", "shortwave_radiation"], date(2010, 1, 1),
                          date(2010, 1, 1), cache_dir=tmp_path, key="s2", session=session)
    assert 65.0 in slept and len(session.calls) == 2


def test_fetch_point_refuses_over_budget(tmp_path):
    with pytest.raises(RuntimeError, match="budget"):
        openmeteo.fetch_point(53.0, 18.6, "era5", ["wind_speed_10m"] * 3, date(2010, 1, 1), date(2015, 12, 31),
                              cache_dir=tmp_path, key="s3", max_daily_weight=10.0, session=FakeSession([]))
