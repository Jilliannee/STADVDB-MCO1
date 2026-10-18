import datetime as dt
import os
 
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
 
from slice_drill import MONTHLY_SQL, WEEKLY_SQL
 

SEED = [
    (dt.date(2025, 1, 6), 10, 10.0),    
    (dt.date(2025, 1, 30), 10, 20.0),  
    (dt.date(2025, 1, 31), 10, 40.0),   
    (dt.date(2025, 2, 3), 20, 30.0),  
    (dt.date(2025, 2, 4), 0, 0.0),     
    (dt.date(2025, 3, 3), 0, 0.0),
    (dt.date(2025, 12, 1), 10, 25.0),  
    (dt.date(2025, 12, 29), 5, 10.0),   
    (dt.date(2025, 12, 30), 5, 10.0), 
    (dt.date(2024, 1, 15), 100, 1000.0),
]
 
@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text("""
            CREATE TABLE dim_date (
                date_key INTEGER PRIMARY KEY, full_date TEXT NOT NULL,
                iso_week INTEGER, month_num INTEGER, year_num INTEGER)"""))
        c.execute(text("""
            CREATE TABLE agg_daily_arrivals (
                date_key INTEGER, arrivals INTEGER, delay_sum REAL)"""))
        for d, arr, delay in SEED:
            key = int(d.strftime("%Y%m%d"))
            c.execute(
                text("INSERT INTO dim_date VALUES (:k, :d, :w, :m, :y)"),
                dict(k=key, d=d.isoformat(), w=d.isocalendar()[1], m=d.month, y=d.year))
            c.execute(
                text("INSERT INTO agg_daily_arrivals VALUES (:k, :a, :s)"),
                dict(k=key, a=arr, s=delay))
    return eng
 
def q(engine, sql, **params):
    return pd.read_sql(sql, engine, params=params)
 
 
# test for monthly
class TestMonthly:
    def test_columns(self, engine):
        df = q(engine, MONTHLY_SQL, yr=2025)
        assert list(df.columns) == ["year_num", "month_num", "arrivals", "avg_delay_min"]
 
    def test_only_requested_year(self, engine):
        df = q(engine, MONTHLY_SQL, yr=2025)
        assert set(df["year_num"]) == {2025}
        assert df["arrivals"].sum() == 70
 
    def test_months_sorted_ascending(self, engine):
        df = q(engine, MONTHLY_SQL, yr=2025)
        assert df["month_num"].tolist() == sorted(df["month_num"].tolist())
 
    def test_only_months_with_data_returned(self, engine):
        df = q(engine, MONTHLY_SQL, yr=2025)
        assert df["month_num"].tolist() == [1, 2, 3, 12]
 
    def test_january_values(self, engine):
        row = q(engine, MONTHLY_SQL, yr=2025).set_index("month_num").loc[1]
        assert row["arrivals"] == 30
        assert row["avg_delay_min"] == pytest.approx(round(70 / 30, 2)) 
 
    def test_avg_is_ratio_of_sums_not_mean_of_daily_means(self, engine):
        row = q(engine, MONTHLY_SQL, yr=2025).set_index("month_num").loc[2]
        assert row["avg_delay_min"] == pytest.approx(1.5)
 
    def test_zero_arrivals_month_gives_null_not_error(self, engine):
        row = q(engine, MONTHLY_SQL, yr=2025).set_index("month_num").loc[3]
        assert row["arrivals"] == 0
        assert pd.isna(row["avg_delay_min"])
 
    def test_december(self, engine):
        row = q(engine, MONTHLY_SQL, yr=2025).set_index("month_num").loc[12]
        assert row["arrivals"] == 20
        assert row["avg_delay_min"] == pytest.approx(2.25)
 
    def test_rounded_to_two_decimals(self, engine):
        for v in q(engine, MONTHLY_SQL, yr=2025)["avg_delay_min"].dropna():
            assert round(v, 2) == v
 
    def test_year_with_no_data_returns_empty(self, engine):
        assert q(engine, MONTHLY_SQL, yr=1999).empty
 
    def test_other_year(self, engine):
        df = q(engine, MONTHLY_SQL, yr=2024)
        assert len(df) == 1
        assert df.iloc[0]["avg_delay_min"] == pytest.approx(10.0)
 
    def test_sql_injection_style_param_is_bound(self, engine):
        try:
            df = q(engine, MONTHLY_SQL, yr="2025; DROP TABLE dim_date")
        except Exception:
            df = pd.DataFrame()
        assert df.empty
        assert q(engine, MONTHLY_SQL, yr=2025).shape[0] == 4 
 
 
# test for weekly
class TestWeekly:
    def test_columns(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=1)
        assert list(df.columns) == ["iso_week", "week_starts_in_month", "arrivals", "avg_delay_min"]
 
    def test_january_weeks(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=1)
        assert df["iso_week"].tolist() == [2, 5]
        assert df["arrivals"].tolist() == [10, 20]
        assert df["avg_delay_min"].tolist() == pytest.approx([1.0, 3.0])
 
    def test_week_start_is_first_day_with_data_in_that_month(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=1)
        assert df["week_starts_in_month"].tolist() == ["2025-01-06", "2025-01-30"]
 
    def test_only_requested_month(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=2)
        assert df["iso_week"].tolist() == [6]
        assert df["arrivals"].tolist() == [20]
 
    def test_only_requested_year(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=1)
        assert df["arrivals"].sum() == 30 
 
    def test_ordered_chronologically_across_iso_year_boundary(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=12)
        assert df["iso_week"].tolist() == [49, 1]
        assert df["arrivals"].tolist() == [10, 10]
        assert df["avg_delay_min"].tolist() == pytest.approx([2.5, 2.0])
 
    def test_zero_arrival_week_gives_null(self, engine):
        df = q(engine, WEEKLY_SQL, yr=2025, mo=3)
        assert len(df) == 1
        assert df.iloc[0]["arrivals"] == 0
        assert pd.isna(df.iloc[0]["avg_delay_min"])
 
    def test_empty_month_returns_empty(self, engine):
        assert q(engine, WEEKLY_SQL, yr=2025, mo=7).empty
 
    def test_weekly_sums_match_monthly_sum(self, engine):
        for mo in (1, 2, 3, 12):
            weekly = q(engine, WEEKLY_SQL, yr=2025, mo=mo)["arrivals"].sum()
            monthly = q(engine, MONTHLY_SQL, yr=2025).set_index("month_num").loc[mo, "arrivals"]
            assert weekly == monthly
 
 
mysql_only = pytest.mark.skipif(
    os.environ.get("RUN_MYSQL_TESTS") != "1",
    reason="set RUN_MYSQL_TESTS=1 to run against the real railway_dw",
)
 
 
@pytest.fixture(scope="module")
def mysql_engine():
    from sqlalchemy.engine import URL
    return create_engine(URL.create(
        "mysql+pymysql",
        username=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 3306)),
        database=os.environ.get("DB_NAME", "railway_dw"),
    ))
 
 
@mysql_only
def test_mysql_monthly_matches_fact_table(mysql_engine):
    """Aggregate table should agree with the fact table it was built from."""
    monthly = q(mysql_engine, MONTHLY_SQL, yr=2025)
    assert not monthly.empty
    fact = pd.read_sql(text("""
        SELECT COUNT(*) AS n FROM fact_stop_event f
        JOIN dim_date d ON d.date_key = f.date_key
        WHERE d.year_num = 2025 AND f.arrival_cancelled = 0
          AND f.arrival_delay_min IS NOT NULL"""), mysql_engine)
    assert monthly["arrivals"].sum() <= fact.iloc[0]["n"] * 1.0 + 1
 
 
@mysql_only
def test_mysql_weekly_sums_match_monthly(mysql_engine):
    monthly = q(mysql_engine, MONTHLY_SQL, yr=2025).set_index("month_num")
    for mo in monthly.index:
        weekly = q(mysql_engine, WEEKLY_SQL, yr=2025, mo=int(mo))
        assert weekly["arrivals"].sum() == monthly.loc[mo, "arrivals"]