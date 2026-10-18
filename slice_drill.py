from sqlalchemy import text

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
