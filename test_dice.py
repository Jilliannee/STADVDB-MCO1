"""Tests for the dice operation dice.py and its section in app.py.
pytest test_dice.py -v
"""
import json
import os
import sys

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dice
from dice import DAY_TYPES, NUMBER_COLS, STATIONS, run_dice

AMS, UTR, ROT = STATIONS
ALL_BANDS = ["Night", "Morning Peak", "Midday", "Evening Peak", "Late"]
COLUMNS = ["station_name", "time_band", "total_stop_events", "cancelled_arrivals",
           "cancelled_departures", "arrival_cancellation_pct"]

# station that is in the dataset but not included in dice
STATION_ROWS = [(1, AMS), (2, UTR), (3, ROT), (4, "Den Haag Centraal")]

DATE_ROWS = [
    (20241231, 0),  # Tue, previous year
    (20250106, 0),  # Mon
    (20250107, 0),  # Tue
    (20250111, 1),  # Sat
    (20250602, 0),  # Mon
    (20260101, 0),  # Thu, next year
]

# date_key, station_key, time_band, stop_events, cancelled_arrivals, cancelled_departures
AGG_ROWS = [
    (20250106, 1, "Morning Peak", 100, 5, 10),
    (20250106, 1, "Evening Peak", 80, 2, 4),
    (20250106, 2, "Morning Peak", 60, 3, 3),
    (20250106, 3, "Midday", 50, 0, 1),
    (20250106, 4, "Morning Peak", 999, 99, 99),
    (20250602, 1, "Morning Peak", 100, 10, 20),
    (20250111, 1, "Morning Peak", 40, 1, 2),      # weekend
    (20250111, 2, "Night", 20, 4, 5),             # weekend
    (20241231, 1, "Morning Peak", 500, 50, 50),   # other year
    (20260101, 1, "Morning Peak", 700, 70, 70),   # other year
    (20250107, 3, "Night", 0, 0, 0),              # zero stop events
]


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("CREATE TABLE dim_station (station_key INTEGER PRIMARY KEY, station_name TEXT)"))
        c.execute(text("CREATE TABLE dim_date (date_key INTEGER PRIMARY KEY, is_weekend INTEGER)"))
        c.execute(text("""CREATE TABLE agg_dice_cancellations (
            date_key INTEGER, station_key INTEGER, time_band TEXT,
            stop_events INTEGER, cancelled_arrivals INTEGER, cancelled_departures INTEGER)"""))
        c.execute(text("INSERT INTO dim_station VALUES (:k, :n)"),
                  [{"k": k, "n": n} for k, n in STATION_ROWS])
        c.execute(text("INSERT INTO dim_date VALUES (:k, :w)"),
                  [{"k": k, "w": w} for k, w in DATE_ROWS])
        c.execute(text("INSERT INTO agg_dice_cancellations VALUES (:d, :s, :b, :e, :a, :c)"),
                  [dict(zip("dsbeac", r)) for r in AGG_ROWS])
    yield eng
    eng.dispose()


def row(df, station, band):
    hit = df[(df.station_name == station) & (df.time_band == band)]
    assert len(hit) == 1, f"expected exactly one row for {station}/{band}, got {len(hit)}"
    return hit.iloc[0]


# constants

def test_constants():
    assert STATIONS == ("Amsterdam Centraal", "Utrecht Centraal", "Rotterdam Centraal")
    assert DAY_TYPES == {"weekday": (0,), "weekend": (1,), "both": (0, 1)}


# shape

def test_result_columns_and_numeric_dtypes(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekday")
    assert list(df.columns) == COLUMNS
    for col in NUMBER_COLS:
        assert pd.api.types.is_numeric_dtype(df[col]), f"{col} should be numeric"


def test_one_row_per_station_and_band(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "both")
    assert not df.duplicated(["station_name", "time_band"]).any()


# weekday/weekend

def test_weekday_only(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekday")
    assert set(zip(df.station_name, df.time_band)) == {
        (AMS, "Morning Peak"), (AMS, "Evening Peak"), (UTR, "Morning Peak"),
        (ROT, "Midday"), (ROT, "Night")}
    # Utrecht's only 2025 Night activity is on a weekend, so it must be absent
    assert not ((df.station_name == UTR) & (df.time_band == "Night")).any()


def test_weekend_only(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekend")
    assert set(zip(df.station_name, df.time_band)) == {(AMS, "Morning Peak"), (UTR, "Night")}
    r = row(df, AMS, "Morning Peak")
    assert (r.total_stop_events, r.cancelled_arrivals, r.cancelled_departures) == (40, 1, 2)


def test_both_is_weekday_plus_weekend(engine):
    both = run_dice(engine, 2025, STATIONS, ALL_BANDS, "both")
    wd = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekday")
    we = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekend")
    for col in ["total_stop_events", "cancelled_arrivals", "cancelled_departures"]:
        assert both[col].sum() == wd[col].sum() + we[col].sum()
    r = row(both, AMS, "Morning Peak")
    assert (r.total_stop_events, r.cancelled_arrivals, r.cancelled_departures) == (240, 16, 32)


def test_default_day_is_weekday(engine):
    default = run_dice(engine, 2025, STATIONS, ALL_BANDS)
    explicit = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekday")
    pd.testing.assert_frame_equal(default, explicit)


def test_invalid_day_raises(engine):
    with pytest.raises(KeyError):
        run_dice(engine, 2025, STATIONS, ALL_BANDS, "holiday")


# year

def test_year_filter_excludes_other_years(engine):
    df = run_dice(engine, 2025, [AMS], ["Morning Peak"], "weekday")
    r = row(df, AMS, "Morning Peak")
    assert r.total_stop_events == 200


def test_year_boundaries_are_inclusive(engine):
    assert row(run_dice(engine, 2024, [AMS], ["Morning Peak"], "weekday"),
               AMS, "Morning Peak").total_stop_events == 500   # Dec 31
    assert row(run_dice(engine, 2026, [AMS], ["Morning Peak"], "weekday"),
               AMS, "Morning Peak").total_stop_events == 700   # Jan 1


def test_year_with_no_data_returns_empty_frame(engine):
    df = run_dice(engine, 1999, STATIONS, ALL_BANDS, "both")
    assert df.empty
    assert list(df.columns) == COLUMNS


# stations / bands

def test_station_subset(engine):
    df = run_dice(engine, 2025, [UTR], ALL_BANDS, "both")
    assert set(df.station_name) == {UTR}


def test_station_outside_the_three_is_never_returned(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "both")
    assert "Den Haag Centraal" not in set(df.station_name)
    assert 999 not in set(df.total_stop_events)


def test_band_subset(engine):
    df = run_dice(engine, 2025, STATIONS, ["Morning Peak", "Midday"], "both")
    assert set(df.time_band) == {"Morning Peak", "Midday"}


def test_single_cell_dice(engine):
    df = run_dice(engine, 2025, [ROT], ["Midday"], "weekday")
    assert len(df) == 1
    r = df.iloc[0]
    assert (r.station_name, r.time_band) == (ROT, "Midday")
    assert (r.total_stop_events, r.cancelled_arrivals, r.cancelled_departures) == (50, 0, 1)


def test_accepts_tuples_and_lists(engine):
    a = run_dice(engine, 2025, tuple(STATIONS), tuple(ALL_BANDS), "both")
    b = run_dice(engine, 2025, list(STATIONS), list(ALL_BANDS), "both")
    pd.testing.assert_frame_equal(a, b)


def test_unknown_band_returns_empty(engine):
    assert run_dice(engine, 2025, STATIONS, ["Teatime"], "both").empty


# aggregation

def test_sums_across_dates(engine):
    r = row(run_dice(engine, 2025, [AMS], ["Morning Peak"], "weekday"), AMS, "Morning Peak")
    assert (r.total_stop_events, r.cancelled_arrivals, r.cancelled_departures) == (200, 15, 30)


def test_pct_is_arrival_cancellations_over_stop_events(engine):
    r = row(run_dice(engine, 2025, [AMS], ["Morning Peak"], "weekday"), AMS, "Morning Peak")
    assert r.arrival_cancellation_pct == pytest.approx(7.5)


def test_pct_rounded_to_two_decimals(engine):
    r = row(run_dice(engine, 2025, [AMS], ["Morning Peak"], "both"), AMS, "Morning Peak")
    assert r.arrival_cancellation_pct == pytest.approx(6.67)


def test_zero_cancellations_gives_zero_pct(engine):
    r = row(run_dice(engine, 2025, [ROT], ["Midday"], "weekday"), ROT, "Midday")
    assert r.arrival_cancellation_pct == 0


def test_zero_stop_events_gives_null_pct_not_error(engine):
    r = row(run_dice(engine, 2025, [ROT], ["Night"], "weekday"), ROT, "Night")
    assert r.total_stop_events == 0
    assert pd.isna(r.arrival_cancellation_pct)


def test_dice_total_matches_raw_rows(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "both")
    manual = sum(r[3] for r in AGG_ROWS if r[0] // 10000 == 2025 and r[1] in (1, 2, 3))
    assert df.total_stop_events.sum() == manual


# ordering

def test_ordered_by_departure_cancellations_desc(engine):
    df = run_dice(engine, 2025, STATIONS, ALL_BANDS, "weekday")
    assert list(df.cancelled_departures) == sorted(df.cancelled_departures, reverse=True)
    assert list(zip(df.station_name, df.time_band)) == [
        (AMS, "Morning Peak"), (AMS, "Evening Peak"), (UTR, "Morning Peak"),
        (ROT, "Midday"), (ROT, "Night")]


def test_ties_broken_by_station_then_band(engine):
    with engine.begin() as c:   # create a 3-way tie on cancelled_departures = 7
        c.execute(text("INSERT INTO agg_dice_cancellations VALUES "
                       "(20250106, 3, 'Late', 10, 1, 7), (20250106, 2, 'Late', 10, 1, 7), "
                       "(20250106, 2, 'Evening Peak', 10, 1, 7)"))
    df = run_dice(engine, 2025, STATIONS, ["Late", "Evening Peak"], "weekday")
    tied = df[df.cancelled_departures == 7]
    assert list(zip(tied.station_name, tied.time_band)) == [
        (ROT, "Late"), (UTR, "Evening Peak"), (UTR, "Late")]


@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("flask")
    pytest.importorskip("pymysql")
    import app as app_module

    calls = []

    def fake(yr, stations, bands, day):
        calls.append((yr, stations, bands, day))
        return json.dumps([{"station_name": stations[0], "time_band": bands[0]}])

    monkeypatch.setattr(app_module, "run_dice_cached", fake)
    c = app_module.app.test_client()
    c.calls = calls
    return c


def test_api_valid_request(client):
    r = client.get("/api/dice", query_string=[("year", 2025), ("station", AMS),
                                              ("band", "Morning Peak"), ("day", "both")])
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    assert r.get_json() == [{"station_name": AMS, "time_band": "Morning Peak"}]
    assert client.calls == [(2025, (AMS,), ("Morning Peak",), "both")]


def test_api_defaults_year_2025_and_weekday(client):
    client.get("/api/dice", query_string=[("station", UTR), ("band", "Night")])
    assert client.calls == [(2025, (UTR,), ("Night",), "weekday")]


def test_api_drops_unknown_stations(client):
    client.get("/api/dice", query_string=[("station", "Den Haag Centraal"), ("station", ROT),
                                          ("band", "Midday")])
    assert client.calls[0][1] == (ROT,)


def test_api_only_unknown_stations_returns_empty_list(client):
    r = client.get("/api/dice", query_string=[("station", "Den Haag Centraal"), ("band", "Midday")])
    assert r.get_json() == []
    assert client.calls == []


def test_api_stations_follow_canonical_order(client):
    client.get("/api/dice", query_string=[("station", ROT), ("station", AMS), ("band", "Midday")])
    assert client.calls[0][1] == (AMS, ROT)


def test_api_bands_are_sorted_for_cache_hits(client):
    client.get("/api/dice", query_string=[("station", AMS), ("band", "Night"),
                                          ("band", "Midday"), ("band", "Late")])
    assert client.calls[0][2] == ("Late", "Midday", "Night")


@pytest.mark.parametrize("qs", [
    [("band", "Midday")],                                        # no station
    [("station", AMS)],                                          # no band
    [("station", AMS), ("band", "Midday"), ("day", "holiday")],  # bad day
    [],
])
def test_api_invalid_inputs_return_empty_list(client, qs):
    r = client.get("/api/dice", query_string=qs)
    assert r.status_code == 200
    assert r.get_json() == []
    assert client.calls == []
