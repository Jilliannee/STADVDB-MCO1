import os
from functools import lru_cache
import pandas as pd
from flask import Flask, Response, render_template, request
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from slice_drill import MONTHLY_SQL, WEEKLY_SQL
from dice import DAY_TYPES, STATIONS, run_dice
from rollup import run_rollup

engine = create_engine(URL.create(
    "mysql+pymysql",
    username=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD", "mysql"), #put the password of your connection here
    host=os.environ.get("DB_HOST", "localhost"),
    port=int(os.environ.get("DB_PORT", 3306)),
    database=os.environ.get("DB_NAME", "railway_dw"),
))

@lru_cache(maxsize=64)
def run(kind, yr, mo=None):
    sql, params = (MONTHLY_SQL, {"yr": yr}) if kind == "monthly" \
        else (WEEKLY_SQL, {"yr": yr, "mo": mo})
    return pd.read_sql(sql, engine, params=params).to_json(orient="records", date_format="iso")

@lru_cache(maxsize=64)
def run_dice_cached(yr, stations, bands, day):
    return run_dice(engine, yr, stations, bands, day).to_json(orient="records")

@lru_cache(maxsize=64)
def run_rollup_cached(yr):
    return run_rollup(engine, yr).to_json(orient="records")

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

@app.route("/api/dice")
def dice():
    # /api/dice?year=2025&station=A&station=B&band=Morning Peak&day=weekday|weekend|both
    asked = request.args.getlist("station")
    stations = tuple(st for st in STATIONS if st in asked)   # only the 3 allowed stations
    bands = tuple(sorted(request.args.getlist("band")))
    day = request.args.get("day", "weekday")
    if not stations or not bands or day not in DAY_TYPES:
        return Response("[]", mimetype="application/json")
    return Response(run_dice_cached(request.args.get("year", 2025, type=int),
                                    stations, bands, day),
                    mimetype="application/json")

@app.route("/api/rollup")
def rollup():
    yr = request.args.get("year", 2025, type=int)
    return Response(run_rollup_cached(yr), mimetype="application/json")

if __name__ == "__main__":
    app.run(debug=True)
