import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from flyby import weather
from flyby.weather import CRUISE_M, fetch_air

WHEN = datetime(2026, 9, 8, 20, 47, tzinfo=timezone.utc)


def reply(elevation=12.0, t2m=26.0, levels=((1000, 207.0, 23.0), (925, 880.0, 17.0), (850, 1594.0, 13.0), (700, 3204.0, 5.5))):
    hourly = {"temperature_2m": [0.0] * 20 + [t2m] + [0.0] * 3}
    for level, height, temp in levels:
        hourly[f"geopotential_height_{level}hPa"] = [None] * 20 + [height] + [None] * 3
        hourly[f"temperature_{level}hPa"] = [None] * 20 + [temp] + [None] * 3
    return json.dumps({"elevation": elevation, "hourly": hourly}).encode()


@pytest.fixture(autouse=True)
def fresh():
    weather._hour.cache_clear()


def serve(monkeypatch, body=None, error=None):
    seen = []

    def opener(req, timeout=None):
        seen.append(req.full_url)
        if error:
            raise error
        return io.BytesIO(body)

    monkeypatch.setattr(weather.urllib.request, "urlopen", opener)
    return seen


def test_temperature_is_read_at_flying_height_not_on_the_ground(monkeypatch):
    seen = serve(monkeypatch, reply())
    air = fetch_air(40.8763, -73.5246, WHEN)
    assert air.temp_at(2.0) == 26.0
    # heights are above the ground (12 m here), and the answer is read off between two levels
    assert air.temp_at(868.0) == pytest.approx(17.0) and air.temp_at(1582.0) == pytest.approx(13.0)
    assert 15.0 < air.temp_at(CRUISE_M) < 17.0
    # the archive is told the place to a tenth of a degree only, and the hour asked for is the one used
    assert "latitude=40.9" in seen[0] and "longitude=-73.5" in seen[0] and "start_date=2026-09-08" in seen[0]


def test_a_pressure_level_below_the_ground_is_left_out(monkeypatch):
    # Denver: the 1000 and 925 hPa levels lie under the surface
    serve(monkeypatch, reply(elevation=1600.0, t2m=20.0,
                             levels=((1000, 110.0, 31.0), (925, 790.0, 27.0), (850, 1500.0, 22.0), (700, 3150.0, 8.0))))
    air = fetch_air(39.7, -105.0, WHEN)
    assert air.heights_m == (2.0, 1550.0) and air.temps_c == (20.0, 8.0)


def test_one_lookup_per_place_and_hour(monkeypatch):
    seen = serve(monkeypatch, reply())
    fetch_air(40.8763, -73.5246, WHEN)
    fetch_air(40.9101, -73.5399, WHEN.replace(minute=5))
    assert len(seen) == 1


@pytest.mark.parametrize("body, error", [(None, urllib.error.URLError("offline")), (b'{"error": true, "reason": "no data"}', None),
                                         (b"not json", None), (reply(levels=()), None)])
def test_no_weather_is_not_an_error(monkeypatch, body, error):
    serve(monkeypatch, body, error)
    assert fetch_air(40.9, -73.5, WHEN) is None


def test_the_air_is_known_up_to_where_turboprops_cruise(monkeypatch):
    # the reference clip's PC-12 passed 5,500 m up: with levels to 3 km only, it was given the air at 3 km
    high = ((1000, 207.0, 23.0), (925, 880.0, 17.0), (850, 1594.0, 13.0), (700, 3204.0, 5.5),
            (600, 4450.0, -2.0), (500, 5890.0, -10.5), (400, 7600.0, -22.0))
    seen = serve(monkeypatch, reply(levels=high))
    air = fetch_air(40.8763, -73.5246, WHEN)
    assert -10.0 < air.temp_at(5514.0) < -7.0 and air.temp_at(9000.0) == -22.0
    assert "temperature_500hPa" in seen[0] and "geopotential_height_400hPa" in seen[0]


def test_a_level_the_archive_leaves_out_does_not_lose_the_rest(monkeypatch):
    serve(monkeypatch, reply())                 # the reply holds four of the seven levels asked for
    assert fetch_air(40.8763, -73.5246, WHEN).heights_m == (2.0, 195.0, 868.0, 1582.0, 3192.0)
