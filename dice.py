import pandas as pd
from sqlalchemy import bindparam, text

# cancellations by station and time band for a chosen year.
#   date: year and weekday/weekend
#   time: selected time bands
#   station: any of the 3 STATIONS 

DAY_TYPES = {"weekday": (0,), "weekend": (1,), "both": (0, 1)}  # dim_date.is_weekend values
STATIONS = ("Amsterdam Centraal", "Utrecht Centraal", "Rotterdam Centraal")  # the only selectable stations

DICE_SQL = text("""
SELECT s.station_name,
       a.time_band,
       SUM(a.stop_events)          AS total_stop_events,
       SUM(a.cancelled_arrivals)   AS cancelled_arrivals,
       SUM(a.cancelled_departures) AS cancelled_departures,
       ROUND(100.0 * SUM(a.cancelled_arrivals)
             / NULLIF(SUM(a.stop_events), 0), 2) AS arrival_cancellation_pct
FROM agg_dice_cancellations a
JOIN dim_station s ON s.station_key = a.station_key
JOIN dim_date    d ON d.date_key    = a.date_key
WHERE s.station_name IN :stations
  AND a.time_band IN :bands
  AND a.date_key BETWEEN :d_from AND :d_to
  AND d.is_weekend IN :day_types
GROUP BY s.station_name, a.time_band
ORDER BY cancelled_departures DESC, s.station_name, a.time_band
""").bindparams(bindparam("stations", expanding=True),
                bindparam("bands", expanding=True),
                bindparam("day_types", expanding=True))

NUMBER_COLS = ["total_stop_events", "cancelled_arrivals",
               "cancelled_departures", "arrival_cancellation_pct"]

def run_dice(engine, year, stations, bands, day="weekday"):
    df = pd.read_sql(DICE_SQL, engine, params={
        "d_from": year * 10000 + 101, "d_to": year * 10000 + 1231,
        "stations": list(stations), "bands": list(bands),
        "day_types": list(DAY_TYPES[day])})
    df[NUMBER_COLS] = df[NUMBER_COLS].apply(pd.to_numeric)  # MySQL SUM() returns Decimal -> JSON strings
    return df
