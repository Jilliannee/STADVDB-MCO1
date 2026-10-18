import pandas as pd
from sqlalchemy import text

# Pivot: average arrival delay by station (rows) and hour of day (columns) for a chosen year.
#   Rows:    the busiest stations (:top)
#   Columns: dim_time.hour_24 (0-23), rotated into 24 columns
#   Measure: SUM(arrival_delay_min) / COUNT(arrivals)  (never an average of averages)
#   Date:    year (:d_from..:d_to)
#
# Optimizations:
#   1. Covering index (PIVOT_INDEX_SQL): the inner query is answered from the index alone.
#   2. Key-range filter: date_key is YYYYMMDD, so a year is one range (a full-year IN-subquery
#      on dim_date made the optimizer skip the covering index in testing).
#   3. Deferred join: aggregate on integer keys first, join station/time labels last.
#
# Rows counted: cancelled arrivals, NULL delays and the first stop (time key -1) are excluded,
# the same rules as agg_table.sql.

PIVOT_INDEX_SQL = """
CREATE INDEX idx_fact_pivot
ON fact_stop_event (station_key, sched_arr_time_key, date_key,
                    arrival_cancelled, arrival_delay_min)
"""

HOURS = range(24)
HOUR_LABELS = [f"{h:02d}:00" for h in HOURS]

def hour_cols(hour, delay, n):
    """The 24 pivot columns h00..h23: average delay of one hour each."""
    return ",\n       ".join(
        f"ROUND(SUM(CASE WHEN {hour} = {h} THEN {delay} END)"
        f" / NULLIF(SUM(CASE WHEN {hour} = {h} THEN {n} END), 0), 2) AS h{h:02d}"
        for h in HOURS)

# Optimized: covering index + key-range filter + deferred join.
PIVOT_SQL = text(f"""
SELECT s.station_name,
       {hour_cols("t.hour_24", "a.delay_sum", "a.n")},
       SUM(a.n) AS total_arrivals
FROM (
    SELECT f.station_key,
           f.sched_arr_time_key     AS time_key,
           COUNT(*)                 AS n,
           SUM(f.arrival_delay_min) AS delay_sum
    FROM fact_stop_event f
    WHERE f.date_key BETWEEN :d_from AND :d_to
      AND f.arrival_cancelled = 0
      AND f.arrival_delay_min IS NOT NULL
      AND f.sched_arr_time_key >= 0
    GROUP BY f.station_key, f.sched_arr_time_key
) a
JOIN dim_station s ON s.station_key = a.station_key
JOIN dim_time    t ON t.time_key    = a.time_key
GROUP BY s.station_key, s.station_name
ORDER BY total_arrivals DESC
LIMIT :top
""")

# Unoptimized (for comparison): every fact row is joined to all three dimensions before grouping.
UNOPTIMIZED_SQL = text(f"""
SELECT s.station_name,
       {hour_cols("t.hour_24", "f.arrival_delay_min", "1")},
       COUNT(*) AS total_arrivals
FROM fact_stop_event f
JOIN dim_station s ON s.station_key = f.station_key
JOIN dim_time    t ON t.time_key    = f.sched_arr_time_key
JOIN dim_date    d ON d.date_key    = f.date_key
WHERE d.year_num = :yr
  AND f.arrival_cancelled = 0
  AND f.arrival_delay_min IS NOT NULL
  AND f.sched_arr_time_key >= 0
GROUP BY s.station_key, s.station_name
ORDER BY total_arrivals DESC
LIMIT :top
""")

def run_pivot(engine, year, top=15, optimized=True):
    sql = PIVOT_SQL if optimized else UNOPTIMIZED_SQL
    df = pd.read_sql(sql, engine, params={
        "yr": year, "d_from": year * 10000 + 101, "d_to": year * 10000 + 1231, "top": top})
    df = df.set_index("station_name").apply(pd.to_numeric)   # MySQL SUM() returns Decimal
    df = df.rename(columns={f"h{h:02d}": HOUR_LABELS[h] for h in HOURS})
    return df  # index = station, columns = "00:00".."23:00" + total_arrivals

if __name__ == "__main__":
    # Timing comparison: python pivot.py  (set DB_* env vars as in app.py)
    import os, time
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    eng = create_engine(URL.create("mysql+pymysql", username=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""), host=os.environ.get("DB_HOST", "localhost"),
        database=os.environ.get("DB_NAME", "railway_dw")))
    for opt in (False, True):
        t0 = time.perf_counter(); df = run_pivot(eng, 2025, 15, opt)
        print(f"{'optimized' if opt else 'unoptimized'}: {(time.perf_counter() - t0) * 1000:,.0f} ms, {len(df)} stations")
