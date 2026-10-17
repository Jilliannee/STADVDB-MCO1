USE MCO1;
DROP TABLE IF EXISTS services_raw;

CREATE TABLE services_raw (
  service_rdt_id       VARCHAR(30),
  service_date         VARCHAR(30),
  service_type         VARCHAR(100),
  service_company      VARCHAR(100),
  train_number         VARCHAR(30),
  completely_cancelled VARCHAR(10),
  partly_cancelled     VARCHAR(10),
  max_delay            VARCHAR(20),
  stop_rdt_id          VARCHAR(30),
  station_code         VARCHAR(20),
  station_name         VARCHAR(150),
  arrival_time         VARCHAR(40),
  arrival_delay        VARCHAR(20),
  arrival_cancelled    VARCHAR(10),
  departure_time       VARCHAR(40),
  departure_delay      VARCHAR(20),
  departure_cancelled  VARCHAR(10),
  platform_change      VARCHAR(10),
  planned_platform     VARCHAR(30),
  actual_platform      VARCHAR(30)
) DEFAULT CHARSET=utf8mb4;

-- the set global stuff will help with loading the data to the warehouse with the ETL 
SET GLOBAL net_write_timeout = 7200;
SET GLOBAL net_read_timeout = 7200;
SET GLOBAL wait_timeout = 28800;
SET GLOBAL innodb_buffer_pool_size = 2147483648;

-- this section loads the csv into the table in mysql
TRUNCATE TABLE services_raw;
LOAD DATA LOCAL INFILE'C:/Users/migue/Downloads/MCO1/services-2025.csv' -- change this to the file path u have
INTO TABLE services_raw
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

