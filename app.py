import os
from functools import lru_cache
import pandas as pd
from flask import Flask, Response, render_template, request
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

engine = create_engine(URL.create(
    "mysql+pymysql",
    username=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD", "obob123"),
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 3306)),
    database=os.environ.get("DB_NAME", "railway_dw"),
))

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

# cache results so repeat clicks don't rerun slow queries
@lru_cache(maxsize=64)
def run(kind, yr, mo=None):
    sql, params = (MONTHLY_SQL, {"yr": yr}) if kind == "monthly" \
        else (WEEKLY_SQL, {"yr": yr, "mo": mo})
    return pd.read_sql(sql, engine, params=params).to_json(orient="records", date_format="iso")

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/monthly")
def monthly():
    return Response(run("monthly", request.args.get("year", 2025, type=int)),
                    mimetype="application/json")

@app.route("/api/weekly")
def weekly():
    return Response(run("weekly", request.args.get("year", 2025, type=int),
                        request.args.get("month", 9, type=int)),
                    mimetype="application/json")

if __name__ == "__main__":
    app.run(debug=True)
