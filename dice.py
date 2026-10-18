import pandas as pd
from sqlalchemy import bindparam, text

# Dice: cancellations by station and time band for a chosen year.
#   Date: year (:yr) and weekday/weekend (:day_types)
#   Time: selected time bands (:bands)
#   Station: any of the 3 allowed STATIONS (:stations)
#
# Optimizations:
#   1. Covering index (DICE_INDEX_SQL): the query is answered from the index alone.
#   2. Dimension pre-filtering: filters run on the small dimension tables first (IN subqueries).
#   3. Deferred join: aggregate on integer keys first, join station/time labels last.

DICE_INDEX_SQL = """
CREATE INDEX idx_fact_dice
ON fact_stop_event (station_key, sched_dep_time_key, date_key,
                    arrival_cancelled, departure_cancelled)
"""

DAY_TYPES = {"weekday": (0,), "weekend": (1,), "both": (0, 1)}  # dim_date.is_weekend values
STATIONS = ("Amsterdam Centraal", "Utrecht Centraal", "Rotterdam Centraal")  # the only selectable stations

DICE_SQL = text("""
SELECT s.station_name,
       t.time_band,
       SUM(a.stop_events)          AS total_stop_events,
       SUM(a.cancelled_arrivals)   AS cancelled_arrivals,
       SUM(a.cancelled_departures) AS cancelled_departures,
       ROUND(100.0 * SUM(a.cancelled_arrivals)
             / NULLIF(SUM(a.stop_events), 0), 2) AS arrival_cancellation_pct
FROM (
    SELECT f.station_key,
           f.sched_dep_time_key       AS time_key,
           COUNT(*)                   AS stop_events,
           SUM(f.arrival_cancelled)   AS cancelled_arrivals,
           SUM(f.departure_cancelled) AS cancelled_departures
    FROM fact_stop_event f
    WHERE f.station_key IN (SELECT station_key FROM dim_station
                            WHERE station_name IN :stations)
      AND f.sched_dep_time_key IN (SELECT time_key FROM dim_time
                                   WHERE time_band IN :bands)
      AND f.date_key IN (SELECT date_key FROM dim_date
                         WHERE year_num = :yr AND is_weekend IN :day_types)
    GROUP BY f.station_key, f.sched_dep_time_key
) a
JOIN dim_station s ON s.station_key = a.station_key
JOIN dim_time    t ON t.time_key    = a.time_key
GROUP BY s.station_name, t.time_band
ORDER BY cancelled_departures DESC, s.station_name, t.time_band
""").bindparams(bindparam("stations", expanding=True),
                bindparam("bands", expanding=True),
                bindparam("day_types", expanding=True))

NUMBER_COLS = ["total_stop_events", "cancelled_arrivals",
               "cancelled_departures", "arrival_cancellation_pct"]

def run_dice(engine, year, stations, bands, day="weekday"):
    df = pd.read_sql(DICE_SQL, engine, params={
        "yr": year, "stations": list(stations), "bands": list(bands),
        "day_types": list(DAY_TYPES[day])})
    df[NUMBER_COLS] = df[NUMBER_COLS].apply(pd.to_numeric)  # MySQL SUM() returns Decimal -> JSON strings
    return df
