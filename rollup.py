from sqlalchemy import bindparam, text
import pandas as pd

ROLLUP_SQL = text("""
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
""").bindparams(bindparam("yr"))

NUMBER_COLS = [
    "total_stop_events", "cancelled_arrivals", "cancelled_departures",
    "total_arrival_delay_min", "avg_arrival_delay_min", "arrival_cancel_pct"
]

def run_rollup(engine, year=2025):
    df = pd.read_sql(ROLLUP_SQL, engine, params={"yr": year})
    df[NUMBER_COLS] = df[NUMBER_COLS].apply(pd.to_numeric)
    return df