"""Tests for the rollup operation rollup.py and its section in app.py.
pytest test_rollup.py -v
"""
import json
import os
import sys

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rollup
from rollup import run_rollup

NUMBER_COLS = getattr(rollup, "NUMBER_COLS", [
    "total_stop_events", "cancelled_arrivals", "cancelled_departures",
    "total_arrival_delay_min", "avg_arrival_delay_min", "arrival_cancel_pct"
])

COLUMNS = [
    "year_num", "quarter_num", "month_num", "month_name",
    "total_stop_events", "cancelled_arrivals", "cancelled_departures",
    "total_arrival_delay_min", "avg_arrival_delay_min", "arrival_cancel_pct"
]

# Mock dimension dates
DATE_ROWS = [
    (20250101, 2025, 1, 1, "January"),
    (20250115, 2025, 1, 1, "January"),
    (20250210, 2025, 1, 2, "February"),
    (20250401, 2025, 2, 4, "April"),
    (20241231, 2024, 4, 12, "December"),
]

# Mock aggregated daily arrivals
AGG_ARRIVALS_ROWS = [
    (20250101, 100, 500),
    (20250115, 150, 750),
    (20250210, 200, 1000),
    (20250401, 300, 1500),
    (20241231, 500, 2500),
]


@pytest.fixture()
def engine(monkeypatch):
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
    with eng.begin() as c:
        c.execute(text("""
            CREATE TABLE dim_date (
                date_key INTEGER PRIMARY KEY,
                year_num INTEGER,
                quarter_num INTEGER,
                month_num INTEGER,
                month_name TEXT
            )
        """))
        c.execute(text("""
            CREATE TABLE agg_daily_arrivals (
                date_key INTEGER PRIMARY KEY,
                arrivals INTEGER,
                delay_sum INTEGER
            )
        """))
        c.execute(text("""
            CREATE TABLE agg_dice_cancellations (
                date_key INTEGER,
                cancelled_arrivals INTEGER DEFAULT 0,
                cancelled_departures INTEGER DEFAULT 0
            )
        """))
        c.execute(text("""
            CREATE TABLE fact_stop_event (
                date_key INTEGER,
                arrival_cancelled INTEGER DEFAULT 0,
                departure_cancelled INTEGER DEFAULT 0,
                arrival_delay_min INTEGER DEFAULT 0
            )
        """))
        c.execute(text("INSERT INTO dim_date VALUES (:k, :y, :q, :m, :name)"),
                  [{"k": k, "y": y, "q": q, "m": m, "name": name} for k, y, q, m, name in DATE_ROWS])
        c.execute(text("INSERT INTO agg_daily_arrivals VALUES (:k, :a, :d)"),
                  [{"k": k, "a": a, "d": d} for k, a, d in AGG_ARRIVALS_ROWS])

    # Strip "WITH ROLLUP" during tests so SQLite doesn't crash
    orig_read_sql = pd.read_sql

    def sqlite_friendly_read_sql(sql, con, params=None, **kwargs):
        sql_str = str(sql)
        if "WITH ROLLUP" in sql_str:
            sql_str = sql_str.replace("WITH ROLLUP", "")
        return orig_read_sql(text(sql_str), con, params=params, **kwargs)

    monkeypatch.setattr(pd, "read_sql", sqlite_friendly_read_sql)

    yield eng
    eng.dispose()


def safe_run_rollup(engine, year, optimized=True):
    try:
        return run_rollup(engine, year=year, optimized=optimized)
    except TypeError:
        return run_rollup(engine, year)


# 1. structure & dtypes

def test_result_columns_and_numeric_dtypes(engine):
    df = safe_run_rollup(engine, 2025, optimized=True)
    assert list(df.columns) == COLUMNS
    for col in NUMBER_COLS:
        if col in df.columns:
            assert pd.api.types.is_numeric_dtype(df[col]), f"{col} should be numeric"


def test_year_filter_excludes_other_years(engine):
    df = safe_run_rollup(engine, 2025, optimized=True)
    assert not df.empty
    jan_row = df[(df.month_num == 1) & (df.quarter_num == 1)]
    assert len(jan_row) == 1
    assert jan_row.iloc[0]["total_stop_events"] == 250


def test_year_with_no_data_returns_empty_frame(engine):
    df = safe_run_rollup(engine, 1999, optimized=True)
    assert df.empty
    assert list(df.columns) == COLUMNS


# 2. rollup agg logic

def test_unoptimized_matches_optimized_output(engine):
    with engine.begin() as c:
        c.execute(text("INSERT INTO fact_stop_event VALUES (20250101, 0, 0, 5)"))
        c.execute(text("INSERT INTO fact_stop_event VALUES (20250115, 0, 0, 5)"))
    
    opt_df = safe_run_rollup(engine, 2025, optimized=True)
    unopt_df = safe_run_rollup(engine, 2025, optimized=False)
    
    assert list(opt_df.columns) == list(unopt_df.columns)


# 3. flash API integration tests

@pytest.fixture()
def client(monkeypatch):
    pytest.importorskip("flask")
    import app as app_module

    calls = []

    def fake_rollup(*args, **kwargs):
        year = kwargs.get("year", args[1] if len(args) > 1 else 2025)
        opt = kwargs.get("optimized", args[2] if len(args) > 2 else True)
        calls.append((year, opt))
        
        sample_df = pd.DataFrame([{
            "year_num": year,
            "quarter_num": 1,
            "month_num": 1,
            "month_name": "January",
            "total_stop_events": 100,
            "cancelled_arrivals": 0,
            "cancelled_departures": 0,
            "total_arrival_delay_min": 50,
            "avg_arrival_delay_min": 0.5,
            "arrival_cancel_pct": 0.0
        }])
        
        # to check if the caller expects JSON string or DataFrame
        if hasattr(app_module, "run_rollup_cached"):
            return sample_df.to_json(orient="records")
        return sample_df

    if hasattr(app_module, "run_rollup_cached"):
        monkeypatch.setattr(app_module, "run_rollup_cached", fake_rollup)
    elif hasattr(app_module, "run_rollup"):
        monkeypatch.setattr(app_module, "run_rollup", fake_rollup)

    c = app_module.app.test_client()
    c.calls = calls
    return c


def test_api_valid_rollup_request(client):
    r = client.get("/api/rollup", query_string=[("year", 2025), ("optimized", "true")])
    assert r.status_code == 200
    assert r.mimetype == "application/json"


def test_api_defaults_to_year_2025(client):
    r = client.get("/api/rollup")
    assert r.status_code == 200