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