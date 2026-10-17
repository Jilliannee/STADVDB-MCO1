CREATE DATABASE railway_dw;
USE railway_dw;

CREATE TABLE dim_date (
  date_key      INT PRIMARY KEY,            -- YYYYMMDD
  full_date     DATE NOT NULL UNIQUE,
  day_of_month  TINYINT NOT NULL,
  day_of_week   TINYINT NOT NULL,           -- 1=Mon..7=Sun
  day_name      VARCHAR(10) NOT NULL,
  iso_week      TINYINT NOT NULL,
  month_num     TINYINT NOT NULL,
  month_name    VARCHAR(10) NOT NULL,
  quarter_num   TINYINT NOT NULL,
  year_num      SMALLINT NOT NULL,
  is_weekend    BOOLEAN NOT NULL
) ENGINE=InnoDB;

CREATE TABLE dim_time (
  time_key      SMALLINT PRIMARY KEY,       -- minutes since midnight (0-1439); -1 = unknown
  hour_24       TINYINT,
  minute_val    TINYINT,
  time_band     VARCHAR(20) NOT NULL        -- Night, Morning Peak, Midday, Evening Peak, Late, Unknown
) ENGINE=InnoDB;

CREATE TABLE dim_station (
  station_key   INT AUTO_INCREMENT PRIMARY KEY,
  station_code  VARCHAR(10) NOT NULL UNIQUE,
  station_name  VARCHAR(100) NOT NULL,
  station_type  VARCHAR(40),                -- from stations CSV, if loaded
  country       VARCHAR(10)                 -- from stations CSV, if loaded
) ENGINE=InnoDB;

CREATE TABLE dim_train (
  train_key      INT AUTO_INCREMENT PRIMARY KEY,
  company        VARCHAR(60) NOT NULL,
  service_type   VARCHAR(40) NOT NULL,
  train_number   INT NOT NULL,
  UNIQUE (company, service_type, train_number)
) ENGINE=InnoDB;

CREATE TABLE fact_stop_event (
  stop_event_key       BIGINT AUTO_INCREMENT PRIMARY KEY,
  date_key             INT NOT NULL,
  train_key            INT NOT NULL,
  station_key          INT NOT NULL,
  sched_arr_time_key   SMALLINT NULL,   -- -1 at the first stop
  sched_dep_time_key   SMALLINT NULL,   -- -1 at the last stop
  service_rdt_id       BIGINT NOT NULL,     -- degenerate dimension
  stop_rdt_id          BIGINT NOT NULL,     -- degenerate dimension
  arrival_delay_min    SMALLINT,
  departure_delay_min  SMALLINT,
  arrival_cancelled    BOOLEAN NOT NULL,
  departure_cancelled  BOOLEAN NOT NULL,
  service_fully_cancelled BOOLEAN NOT NULL,
  service_partly_cancelled BOOLEAN NOT NULL,
  platform_changed     BOOLEAN NOT NULL,
  is_arrival_late      BOOLEAN NOT NULL,    -- derived in ETL (threshold is your decision)
  CONSTRAINT fk_f_date  FOREIGN KEY (date_key)           REFERENCES dim_date(date_key),
  CONSTRAINT fk_f_train FOREIGN KEY (train_key)          REFERENCES dim_train(train_key),
  CONSTRAINT fk_f_stat  FOREIGN KEY (station_key)        REFERENCES dim_station(station_key),
  CONSTRAINT fk_f_arr   FOREIGN KEY (sched_arr_time_key) REFERENCES dim_time(time_key),
  CONSTRAINT fk_f_dep   FOREIGN KEY (sched_dep_time_key) REFERENCES dim_time(time_key)
) ENGINE=InnoDB;

