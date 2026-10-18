import os
from functools import lru_cache
import pandas as pd
from flask import Flask, Response, render_template, request
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from queries import MONTHLY_SQL, WEEKLY_SQL

engine = create_engine(URL.create(
    "mysql+pymysql",
    username=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD", "   "), #put the password of your connection here
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 3306)),
    database=os.environ.get("DB_NAME", "railway_dw"),
))

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
