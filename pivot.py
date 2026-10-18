import pandas as pd
from sqlalchemy import text

# Average arrival delay by station and hour, with stations as rows and hours as columns 

PIVOT_SQL = text("""
SELECT
    s.station_name,
    t.hour_num,
    AVG(f.arrival_delay_min) AS avg_delay_min
FROM fact_stop_event f
JOIN dim_station s
    ON s.station_key = f.station_key
JOIN dim_time t
    ON t.time_key = f.time_key
GROUP BY
    s.station_name,
    t.hour_num
ORDER BY
    s.station_name,
    t.hour_num
""")

def get_station_hour_pivot(engine):
    df = pd.read_sql(
        PIVOT_SQL,
        engine
    )

    pivot_df = df.pivot_table(
        index="station_name",
        columns="hour_num",
        values="avg_delay_min",
        aggfunc="mean"
    )

    pivot_df.columns = [
        f"{int(hour):02d}:00"
        for hour in pivot_df.columns
    ]

    return pivot_df.round(2)