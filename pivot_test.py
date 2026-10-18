import os
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
import pivot
from pivot import HOUR_LABELS, HOURS, PIVOT_INDEX_SQL, hour_cols, run_pivot
 
BOTH_MODES = pytest.mark.parametrize("optimized", [True, False], ids=["optimized", "unoptimized"])
 
@pytest.fixture()
def seeded_engine():

    eng = create_engine("sqlite://", poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE dim_station (station_key INTEGER PRIMARY KEY, station_name TEXT)"))
        c.execute(text("CREATE TABLE dim_time (time_key INTEGER PRIMARY KEY, hour_24 INTEGER)"))
        c.execute(text("CREATE TABLE dim_date (date_key INTEGER PRIMARY KEY, year_num INTEGER)"))
        # arrival_delay_min is REAL: SQLite would otherwise do integer division,
        # which MySQL does not.
        c.execute(text("""
            CREATE TABLE fact_stop_event (
                station_key INTEGER, sched_arr_time_key INTEGER, date_key INTEGER,
                arrival_cancelled INTEGER, arrival_delay_min REAL)"""))
 
        c.execute(text("INSERT INTO dim_station VALUES (1,'A'),(2,'B'),(3,'C'),(4,'D')"))
        c.execute(text("INSERT INTO dim_time VALUES (800,8),(830,8),(900,9),(1700,17)"))
        c.execute(text("""INSERT INTO dim_date VALUES
            (20241231,2024),(20250101,2025),(20250615,2025),(20251231,2025),(20260101,2026)"""))
 
        rows = [
        
            (1, 800, 20250101, 0, 2),
            (1, 830, 20250615, 0, 4),
            (1, 830, 20250615, 0, 6),
            (1, 830, 20251231, 0, 8),        
            (1, 900, 20250615, 0, 3),
            (1, 900, 20250615, 0, 2),
      
            (1, 800, 20250615, 1, 50),         
            (1, 800, 20250615, 0, None),      
            (1, -1, 20250615, 0, 99),          
            (1, 800, 20241231, 0, 100),        
            (1, 800, 20260101, 0, 200),       
       
            (3, 800, 20250615, 0, 1),
            (3, 800, 20250615, 0, 3),
            (2, 1700, 20250615, 0, 10),
            (4, 800, 20241231, 0, 7),       
        ]
        c.execute(text("INSERT INTO fact_stop_event VALUES (:s,:t,:d,:c,:x)"),
                  [dict(s=s, t=t, d=d, c=cn, x=x) for s, t, d, cn, x in rows])
    yield eng
    eng.dispose()
 
class TestHourCols:
    def test_generates_24_aliased_columns(self):
        sql = hour_cols("t.hour_24", "a.delay_sum", "a.n")
        for h in HOURS:
            assert f"AS h{h:02d}" in sql
        assert sql.count("ROUND(") == 24
 
    def test_guards_against_divide_by_zero(self):
        assert hour_cols("h", "d", "n").count("NULLIF(") == 24
 
    def test_hour_labels(self):
        assert len(HOUR_LABELS) == 24
        assert HOUR_LABELS[0] == "00:00"
        assert HOUR_LABELS[9] == "09:00"
        assert HOUR_LABELS[23] == "23:00"
 
@BOTH_MODES
def test_shape_and_labels(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 2025, top=15, optimized=optimized)
    assert df.index.name == "station_name"
    assert list(df.columns) == HOUR_LABELS + ["total_arrivals"]
    # every cell is numeric (MySQL returns Decimal; run_pivot must convert it)
    assert all(pd.api.types.is_numeric_dtype(df[col]) for col in df.columns)
 
@BOTH_MODES
def test_known_averages(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert df.loc["A", "08:00"] == pytest.approx(5.0)
    assert df.loc["A", "09:00"] == pytest.approx(2.5)
    assert df.loc["C", "08:00"] == pytest.approx(2.0)
    assert df.loc["B", "17:00"] == pytest.approx(10.0)
 
 
@BOTH_MODES
def test_is_not_an_average_of_averages(seeded_engine, optimized):
    """Hour 8 for A spans two time keys (800 and 830). Weighted avg = 5.0, not 4.0."""
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert df.loc["A", "08:00"] != pytest.approx(4.0)
    assert df.loc["A", "08:00"] == pytest.approx(5.0)
 
 
@BOTH_MODES
def test_hours_without_arrivals_are_null(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert pd.isna(df.loc["A", "00:00"])
    assert pd.isna(df.loc["A", "23:00"])
    assert pd.isna(df.loc["B", "08:00"])
 
 
@BOTH_MODES
def test_total_arrivals(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert df["total_arrivals"].to_dict() == {"A": 6, "C": 2, "B": 1}
 
@BOTH_MODES
def test_excludes_cancelled_null_first_stop(seeded_engine, optimized):
    """A's junk rows (delay 50 / NULL / 99) would wreck the 08:00 average and the count."""
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert df.loc["A", "08:00"] == pytest.approx(5.0)
    assert df.loc["A", "total_arrivals"] == 6
 
 
@BOTH_MODES
def test_year_boundaries(seeded_engine, optimized):
    """2024-12-31 and 2026-01-01 rows must be dropped; 2025-01-01 and 2025-12-31 kept."""
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert df.loc["A", "total_arrivals"] == 6       
    assert "D" not in df.index                       
 
 
@BOTH_MODES
def test_other_year(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 2024, optimized=optimized)
    assert set(df.index) == {"A", "D"}
    assert df.loc["A", "08:00"] == pytest.approx(100.0)
    assert df.loc["D", "08:00"] == pytest.approx(7.0)
 
 
@BOTH_MODES
def test_year_with_no_data(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 1999, optimized=optimized)
    assert df.empty
 
@BOTH_MODES
def test_ordered_by_total_arrivals_desc(seeded_engine, optimized):
    df = run_pivot(seeded_engine, 2025, optimized=optimized)
    assert list(df.index) == ["A", "C", "B"]
    assert df["total_arrivals"].is_monotonic_decreasing
 
 
@BOTH_MODES
def test_top_limits_rows(seeded_engine, optimized):
    assert list(run_pivot(seeded_engine, 2025, top=2, optimized=optimized).index) == ["A", "C"]
    assert list(run_pivot(seeded_engine, 2025, top=1, optimized=optimized).index) == ["A"]
 
def test_optimized_matches_unoptimized(seeded_engine):
    """The optimization must not change results."""
    fast = run_pivot(seeded_engine, 2025, top=15, optimized=True)
    slow = run_pivot(seeded_engine, 2025, top=15, optimized=False)
    pd.testing.assert_frame_equal(fast, slow, check_dtype=False)
 
def test_index_sql_is_valid(seeded_engine):
    with seeded_engine.begin() as c:
        c.execute(text(PIVOT_INDEX_SQL))
        names = [r[1] for r in c.execute(text("PRAGMA index_list(fact_stop_event)"))]
    assert "idx_fact_pivot" in names
 
def test_queries_still_work_with_index(seeded_engine):
    with seeded_engine.begin() as c:
        c.execute(text(PIVOT_INDEX_SQL))
    df = run_pivot(seeded_engine, 2025)
    assert df.loc["A", "08:00"] == pytest.approx(5.0)
 
@pytest.mark.skipif(os.environ.get("PIVOT_TEST_MYSQL") != "1",
                    reason="set PIVOT_TEST_MYSQL=1 (and DB_* vars) to run against MySQL")
def test_mysql_optimized_matches_unoptimized():
    from sqlalchemy.engine import URL
    eng = create_engine(URL.create(
        "mysql+pymysql",
        username=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 3306)),
        database=os.environ.get("DB_NAME", "railway_dw")))
    fast = run_pivot(eng, 2025, top=10, optimized=True)
    slow = run_pivot(eng, 2025, top=10, optimized=False)
    assert len(fast) > 0
    pd.testing.assert_frame_equal(fast, slow, check_dtype=False, atol=0.01)