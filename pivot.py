import pandas as pd
from sqlalchemy import create_engine, text

# Average arrival delay by station and hour, with stations as rows and hours as columns 
USER = "root"
PASSWORD = "Nico_di_Angelo13"
HOST = "localhost"
PORT = 3306
DB = "railway_dw"

engine = create_engine(f"mysql+pymysql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB}")

PIVOT_SQL = text("""
SELECT
    s.station_name,
    t.hour_24,
    AVG(f.arrival_delay_min) AS avg_delay_min
FROM fact_stop_event f
JOIN dim_station s
    ON s.station_key = f.station_key
JOIN dim_time t
    ON t.sched_arr_time_key = f.time_key
GROUP BY
    s.station_name,
    t.hour_24
ORDER BY
    s.station_name,
    t.hour_24
""")

def get_station_hour_pivot(engine):
    df = pd.read_sql(PIVOT_SQL, engine)

    pivot_df = df.pivot_table(
        index="station_name",
        columns="hour_24",
        values="avg_delay_min",
        aggfunc="mean"
    )

    pivot_df.columns = [
        f"{int(hour):02d}:00"
        for hour in pivot_df.columns
    ]

    return pivot_df.round(2)