import os
import pandas as pd
from sqlalchemy import create_engine, text

# Connection: adjust user/host/port. Database is railway_dw (change if the tables are elsewhere)
USER = "root"
PASSWORD = "obob123"
HOST = "localhost"
PORT = 3306
DB = "railway_dw"

engine = create_engine(f"mysql+pymysql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB}")

MONTHLY_SQL = text("""
SELECT d.year_num, d.month_num,
       SUM(a.arrivals) AS arrivals,
       ROUND(SUM(a.delay_sum) / SUM(a.arrivals), 2) AS avg_delay_min
FROM agg_daily_arrivals a JOIN dim_date d ON d.date_key = a.date_key
WHERE d.year_num = :yr
GROUP BY d.year_num, d.month_num ORDER BY d.month_num
""")

WEEKLY_SQL = text("""
SELECT d.iso_week, MIN(d.full_date) AS week_starts_in_month,
       SUM(a.arrivals) AS arrivals,
       ROUND(SUM(a.delay_sum) / SUM(a.arrivals), 2) AS avg_delay_min
FROM agg_daily_arrivals a JOIN dim_date d ON d.date_key = a.date_key
WHERE d.year_num = :yr AND d.month_num = :mo
GROUP BY d.iso_week ORDER BY week_starts_in_month
""")

def monthly_summary(year: int) -> pd.DataFrame:
    return pd.read_sql(MONTHLY_SQL, engine, params={"yr": year})

def weekly_summary(year: int, month: int) -> pd.DataFrame:
    return pd.read_sql(WEEKLY_SQL, engine, params={"yr": year, "mo": month})

if __name__ == "__main__":
    monthly = monthly_summary(2025)
    weekly = weekly_summary(2025, 9)

    print(monthly)
    print(weekly)

    # Optional: save results
    monthly.to_csv("monthly_2025.csv", index=False)
    weekly.to_csv("weekly_2025_09.csv", index=False)