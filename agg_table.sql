USE railway_dw;

DROP TABLE IF EXISTS agg_daily_arrivals;

CREATE TABLE agg_daily_arrivals AS
SELECT date_key,
       COUNT(*) AS arrivals,
       SUM(arrival_delay_min) AS delay_sum
FROM fact_stop_event
WHERE arrival_cancelled = 0 AND arrival_delay_min IS NOT NULL
GROUP BY date_key;

ALTER TABLE agg_daily_arrivals ADD PRIMARY KEY (date_key);

DROP TABLE IF EXISTS agg_dice_cancellations;

CREATE TABLE agg_dice_cancellations (
  station_key          INT         NOT NULL,
  date_key             INT         NOT NULL,   -- YYYYMMDD
  time_band            VARCHAR(20) NOT NULL,
  stop_events          INT         NOT NULL,
  cancelled_arrivals   INT         NOT NULL,
  cancelled_departures INT         NOT NULL,
  PRIMARY KEY (station_key, date_key, time_band)
) ENGINE=InnoDB;

INSERT INTO agg_dice_cancellations
SELECT f.station_key,
       f.date_key,
       t.time_band,
       COUNT(*),
       SUM(f.arrival_cancelled),
       SUM(f.departure_cancelled)
FROM fact_stop_event f
JOIN dim_time t ON t.time_key = f.sched_dep_time_key
GROUP BY f.station_key, f.date_key, t.time_band
ORDER BY f.station_key, f.date_key, t.time_band;

ANALYZE TABLE agg_dice_cancellations;
