import os
import time
import pandas as pd
from sqlalchemy import bindparam, create_engine, text

# Unoptimized query: scans the raw fact_stop_event table directly
UNOPTIMIZED_ROLLUP_SQL = text("""
SELECT 
    d.year_num,
    d.quarter_num,
    d.month_num,
    CASE 
        WHEN d.month_num IS NULL THEN 'Subtotal' 
        ELSE MAX(d.month_name) 
    END AS month_name,
    COUNT(*) AS total_stop_events,
    SUM(f.arrival_cancelled) AS cancelled_arrivals,
    SUM(f.departure_cancelled) AS cancelled_departures,
    SUM(f.arrival_delay_min) AS total_arrival_delay_min,
    ROUND(AVG(f.arrival_delay_min), 2) AS avg_arrival_delay_min,
    ROUND(100.0 * SUM(f.arrival_cancelled) / NULLIF(COUNT(*), 0), 2) AS arrival_cancel_pct
FROM fact_stop_event f
JOIN dim_date d ON d.date_key = f.date_key
WHERE d.year_num = :yr
GROUP BY d.year_num, d.quarter_num, d.month_num WITH ROLLUP
HAVING d.year_num IS NOT NULL OR (d.quarter_num IS NULL AND d.month_num IS NULL)
ORDER BY d.year_num, d.quarter_num, d.month_num
""")

# Optimized query: uses pre-aggregated tables to reduce the amount of data scanned
OPTIMIZED_ROLLUP_SQL = text("""
SELECT 
    d.year_num,
    d.quarter_num,
    d.month_num,
    CASE 
        WHEN d.month_num IS NULL THEN 'Subtotal' 
        ELSE MAX(d.month_name) 
    END AS month_name,
    (SUM(a.arrivals) + COALESCE(SUM(c.cancelled_arrivals), 0)) AS total_stop_events,
    COALESCE(SUM(c.cancelled_arrivals), 0) AS cancelled_arrivals,
    COALESCE(SUM(c.cancelled_departures), 0) AS cancelled_departures,
    SUM(a.delay_sum) AS total_arrival_delay_min,
    ROUND(SUM(a.delay_sum) / NULLIF(SUM(a.arrivals), 0), 2) AS avg_arrival_delay_min,
    ROUND(100.0 * COALESCE(SUM(c.cancelled_arrivals), 0) / NULLIF(SUM(a.arrivals) + COALESCE(SUM(c.cancelled_arrivals), 0), 0), 2) 
                            AS arrival_cancel_pct
FROM agg_daily_arrivals a
JOIN dim_date d ON d.date_key = a.date_key
LEFT JOIN (
    SELECT date_key, 
           SUM(cancelled_arrivals) AS cancelled_arrivals, 
           SUM(cancelled_departures) AS cancelled_departures
    FROM agg_dice_cancellations
    GROUP BY date_key
) c ON c.date_key = a.date_key
WHERE a.date_key BETWEEN :d_from AND :d_to
GROUP BY d.year_num, d.quarter_num, d.month_num WITH ROLLUP
HAVING d.year_num IS NOT NULL OR (d.quarter_num IS NULL AND d.month_num IS NULL)
ORDER BY d.year_num, d.quarter_num, d.month_num
""")

NUMBER_COLS = [
    "total_stop_events", "cancelled_arrivals", "cancelled_departures",
    "total_arrival_delay_min", "avg_arrival_delay_min", "arrival_cancel_pct"
]

def run_rollup(engine, year=2025, optimized=True):
    if optimized:
        df = pd.read_sql(OPTIMIZED_ROLLUP_SQL, engine, params={
            "d_from": year * 10000 + 101, 
            "d_to": year * 10000 + 1231
        })
    else:
        df = pd.read_sql(UNOPTIMIZED_ROLLUP_SQL, engine, params={"yr": year})
        
    df[NUMBER_COLS] = df[NUMBER_COLS].apply(pd.to_numeric)
    return df

if __name__ == "__main__":
    db_user = os.environ.get("DB_USER", "root")
    db_pass = os.environ.get("DB_PASSWORD", "")  
    db_host = os.environ.get("DB_HOST", "localhost")
    db_name = os.environ.get("DB_NAME", "railway_dw")
    
    db_url = f"mysql+pymysql://{db_user}:{db_pass}@{db_host}/{db_name}"
    eng = create_engine(db_url)
    
    # Runs only optimized query for fast result
    t0 = time.perf_counter()
    df = run_rollup(eng, year=2025, optimized=True)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"optimized: {elapsed:,.2f} ms ({elapsed/1000:,.3f} sec), {len(df)} rows")