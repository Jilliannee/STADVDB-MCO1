import argparse
import getpass
import logging
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd
import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# =============================================================================
# CONFIG
# =============================================================================


def _env(name, default=None):
    return os.environ.get(name, default)


SRC = {
    "host": _env("SRC_HOST", "localhost"),
    "port": int(_env("SRC_PORT", "3306")),
    "user": _env("SRC_USER", "root"),
    "password": _env("SRC_PASSWORD"),
    "database": _env("SRC_DB", "mco1"),
}
DW = {
    "host": _env("DW_HOST", "localhost"),
    "port": int(_env("DW_PORT", "3306")),
    "user": _env("DW_USER", "root"),
    "password": _env("DW_PASSWORD"),
    "database": _env("DW_DB", "railway_dw"),
}

SRC_TABLE = "services_raw"

SRC_COLS = {
    "service_rdt_id": "service_rdt_id",
    "service_date": "service_date",
    "service_type": "service_type",
    "company": "service_company",
    "train_number": "train_number",
    "completely_cancelled": "completely_cancelled",
    "partly_cancelled": "partly_cancelled",
    "stop_rdt_id": "stop_rdt_id",
    "station_code": "station_code",
    "station_name": "station_name",
    "arrival_time": "arrival_time",
    "arrival_delay": "arrival_delay",
    "arrival_cancelled": "arrival_cancelled",
    "departure_time": "departure_time",
    "departure_delay": "departure_delay",
    "departure_cancelled": "departure_cancelled",
    "platform_change": "platform_change",
}

CHUNK_SIZE = 200_000      # rows from the source per iteration
INSERT_BATCH = 20_000     # rows per multi-row INSERT into the warehouse

LATE_THRESHOLD_MIN = 3
NULLIFY_DELAY_WHEN_CANCELLED = True
SMALLINT_MAX = 32767

REJECT_FILE = "etl_rejects.csv"
LOG_FILE = "etl.log"

FACT_SRC_COLS = [
    "service_rdt_id", "service_date", "service_type", "company", "train_number",
    "completely_cancelled", "partly_cancelled", "stop_rdt_id",
    "station_code", "station_name",
    "arrival_time", "arrival_delay", "arrival_cancelled",
    "departure_time", "departure_delay", "departure_cancelled", "platform_change",
]

FACT_COLS = [
    "date_key", "train_key", "station_key", "sched_arr_time_key", "sched_dep_time_key",
    "service_rdt_id", "stop_rdt_id", "arrival_delay_min", "departure_delay_min",
    "arrival_cancelled", "departure_cancelled", "service_fully_cancelled",
    "service_partly_cancelled", "platform_changed", "is_arrival_late",
]

EXPECTED_SCHEMA = {
    "dim_date": {"date_key", "full_date", "day_of_month", "day_of_week", "day_name",
                 "iso_week", "month_num", "month_name", "quarter_num", "year_num",
                 "is_weekend"},
    "dim_time": {"time_key", "hour_24", "minute_val", "time_band"},
    "dim_station": {"station_key", "station_code", "station_name"},
    "dim_train": {"train_key", "company", "service_type", "train_number"},
    "fact_stop_event": set(FACT_COLS) | {"stop_event_key"},
}

log = logging.getLogger("etl")


# =============================================================================
# HELPERS
# =============================================================================


def select_list(logical_cols):
    """SELECT list that aliases source columns back to the logical names."""
    return ", ".join(f"`{SRC_COLS[c]}` AS `{c}`" for c in logical_cols)


def clean_text(ser, fill=None):
    """Trim, collapse inner whitespace, turn '' into NULL, optionally fill NULLs."""
    out = ser.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    out = out.mask((out == "").fillna(False), pd.NA)
    if fill is not None:
        out = out.fillna(fill)
    return out.astype(object).where(out.notna(), None)


_TS_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"


def time_key(ser):
    """
    SPLIT an ISO timestamp such as '2025-01-01T02:12:00+01:00' into hour/minute
    and return minutes-since-midnight (0..1439). The offset in the string is
    already Europe/Amsterdam local time, so the wall-clock digits are used as is.
    Missing/unparseable -> -1 (the 'Unknown' member of dim_time).
    Returns (key as int16, mask of non-null-but-unparseable values).
    """
    s = ser.astype(object)
    ok = s.str.match(_TS_RE, na=False)
    hour = pd.to_numeric(s.str.slice(11, 13), errors="coerce")
    minute = pd.to_numeric(s.str.slice(14, 16), errors="coerce")
    valid = ok & hour.between(0, 23) & minute.between(0, 59)
    key = (hour * 60 + minute).where(valid, -1).fillna(-1).astype("int16")
    return key, (ser.notna() & ~valid)


_BOOL_MAP = {"true": 1, "false": 0, "1": 1, "0": 0, "t": 1, "f": 0, "yes": 1, "no": 0}


def to_flag(ser):
    """Convert known Boolean forms to 0/1; return flag, null mask, and invalid-value mask."""
    s = ser.astype("string").str.strip().str.lower()  # preserve nullable strings for validation
    mapped = s.map(_BOOL_MAP)
    null_mask = ser.isna() | s.eq("").fillna(False)
    invalid_mask = (~null_mask) & mapped.isna()  # distinguish invalid values
    flags = pd.to_numeric(mapped, errors="coerce").fillna(0).astype("int8")
    return flags, null_mask, invalid_mask


def time_band(hour):
    if hour < 6:
        return "Night"
    if hour < 9:
        return "Morning Peak"
    if hour < 16:
        return "Midday"
    if hour < 19:
        return "Evening Peak"
    return "Late"


def to_rows(df, cols):
    """DataFrame -> list of tuples with NaN/NA converted to None (for pymysql)."""
    sub = df[cols].astype(object)
    sub = sub.where(df[cols].notna(), None)
    return [tuple(r) for r in sub.values.tolist()]


def bulk_insert(cur, table, columns, rows, ignore=False, batch=INSERT_BATCH):
    verb = "INSERT IGNORE" if ignore else "INSERT"
    sql = (f"{verb} INTO {table} ({', '.join(columns)}) "
           f"VALUES ({', '.join(['%s'] * len(columns))})")
    for i in range(0, len(rows), batch):
        cur.executemany(sql, rows[i:i + batch])


# =============================================================================
# CJECK SCHEMA
# =============================================================================


def check_schema(cur):
    for table, needed in EXPECTED_SCHEMA.items():
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s", (table,))
        have = {r[0].lower() for r in cur.fetchall()}
        if not have:
            raise SystemExit(f"Table '{table}' not found in '{DW['database']}'. "
                             f"Create the warehouse schema first.")
        missing = needed - have
        if missing:
            raise SystemExit(f"Table '{table}' is missing columns: {sorted(missing)}")


# =============================================================================
# LOAD DIMENSIONS
# =============================================================================


def load_dim_date(src_conn, cur):
    col = SRC_COLS["service_date"]
    r = pd.read_sql_query(text(f"SELECT MIN(`{col}`) AS lo, MAX(`{col}`) AS hi "
                               f"FROM `{SRC_TABLE}`"), src_conn)
    lo, hi = pd.to_datetime(r.loc[0, "lo"]), pd.to_datetime(r.loc[0, "hi"])
    if pd.isna(lo) or pd.isna(hi):
        raise SystemExit("Source table has no valid service dates - was the import successful?")
    rng = pd.date_range(f"{lo.year}-01-01", f"{hi.year}-12-31", freq="D")
    d = pd.DataFrame({"full_date": rng})
    d["date_key"] = d.full_date.dt.year * 10000 + d.full_date.dt.month * 100 + d.full_date.dt.day
    d["day_of_month"] = d.full_date.dt.day
    d["day_of_week"] = d.full_date.dt.dayofweek + 1            # 1=Mon..7=Sun
    d["day_name"] = d.full_date.dt.day_name()
    d["iso_week"] = d.full_date.dt.isocalendar().week.astype(int)
    d["month_num"] = d.full_date.dt.month
    d["month_name"] = d.full_date.dt.month_name()
    d["quarter_num"] = d.full_date.dt.quarter
    d["year_num"] = d.full_date.dt.year
    d["is_weekend"] = (d.full_date.dt.dayofweek >= 5).astype(int)
    d["full_date"] = d.full_date.dt.date
    cols = ["date_key", "full_date", "day_of_month", "day_of_week", "day_name", "iso_week",
            "month_num", "month_name", "quarter_num", "year_num", "is_weekend"]
    bulk_insert(cur, "dim_date", cols, to_rows(d, cols), ignore=True)
    log.info("dim_date: %s calendar days (%s .. %s)", len(d), d.full_date.iloc[0], d.full_date.iloc[-1])
    return d["date_key"].to_numpy()


def load_dim_time(cur):
    rows = [(-1, None, None, "Unknown")]
    rows += [(m, m // 60, m % 60, time_band(m // 60)) for m in range(1440)]
    bulk_insert(cur, "dim_time", ["time_key", "hour_24", "minute_val", "time_band"],
                rows, ignore=True)
    log.info("dim_time: 1440 minutes + 1 Unknown member")


def load_dim_station(src_conn, cur, stats):
    sel = select_list(["station_code", "station_name"])
    raw = pd.read_sql_query(text(f"SELECT DISTINCT {sel} FROM `{SRC_TABLE}`"), src_conn)
    raw["station_name"] = clean_text(raw["station_name"])
    raw["station_code"] = clean_text(raw["station_code"])

    missing_codes = raw["station_code"].isna()
    stats["source_station_rows_missing_code"] += int(missing_codes.sum())
    raw = raw.dropna(subset=["station_code", "station_name"])
    raw = raw.drop_duplicates(subset=["station_code"])

    bulk_insert(cur, "dim_station", ["station_code", "station_name"],
                to_rows(raw, ["station_code", "station_name"]), ignore=True)

    #map station codes to surrogate keys
    cur.execute("SELECT station_code, station_key FROM dim_station")
    station_map = dict(cur.fetchall())
    missing = set(raw["station_code"]) - set(station_map)
    if missing:
        raise RuntimeError(f"{len(missing)} station codes were not loaded into dim_station; "
                           f"examples: {sorted(missing)[:3]}")
    log.info("dim_station: %s stations; source rows missing station codes: %s",
             len(station_map), stats["source_station_rows_missing_code"])
    return station_map


def load_dim_train(src_conn, cur):
    sel = select_list(["company", "service_type", "train_number"])
    raw = pd.read_sql_query(text(f"SELECT DISTINCT {sel} FROM `{SRC_TABLE}`"), src_conn)
    raw["company"] = clean_text(raw["company"], fill="Unknown")
    raw["service_type"] = clean_text(raw["service_type"], fill="Unknown")
    raw["train_number"] = pd.to_numeric(raw["train_number"], errors="coerce")
    raw = raw.dropna(subset=["train_number"])
    raw["train_number"] = raw["train_number"].astype("int64")
    raw = raw.drop_duplicates()
    cols = ["company", "service_type", "train_number"]
    bulk_insert(cur, "dim_train", cols, to_rows(raw, cols), ignore=True)
    cur.execute("SELECT train_key, company, service_type, train_number FROM dim_train")
    tdf = pd.DataFrame(cur.fetchall(), columns=["train_key", "company", "service_type", "train_number"])
    tdf["train_number"] = tdf["train_number"].astype("int64")
    tdf["company"] = tdf["company"].astype(object)
    tdf["service_type"] = tdf["service_type"].astype(object)
    if len(tdf) < len(raw):
        log.warning("dim_train has fewer rows (%s) than distinct source combinations (%s); "
                    "a case-insensitive collation may be merging values.", len(tdf), len(raw))
    log.info("dim_train: %s company/service-type/train-number combinations", len(tdf))
    return tdf


# =============================================================================
# TRANSFORM
# =============================================================================


def transform_chunk(raw, station_map, train_df, date_keys, stats):
    raw = raw.reset_index(drop=True)
    df = raw.copy()
    stats["extracted"] += len(df)

    # CLEAN whitespace, blanks, NULL, etc
    df["station_name"] = clean_text(df["station_name"])
    df["station_code"] = clean_text(df["station_code"])  
    comp = clean_text(df["company"])
    stats["filled_unknown_company"] += int(comp.isna().sum())
    df["company"] = comp.fillna("Unknown")
    stype = clean_text(df["service_type"])
    stats["filled_unknown_service_type"] += int(stype.isna().sum())
    df["service_type"] = stype.fillna("Unknown")

    # TYPE CONVERSION
    for c in ("service_rdt_id", "stop_rdt_id", "train_number"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    d = pd.to_datetime(df["service_date"], errors="coerce")
    df["date_key"] = d.dt.year * 10000 + d.dt.month * 100 + d.dt.day

    # SPLIT
    df["sched_arr_time_key"], bad_arr = time_key(df["arrival_time"])
    df["sched_dep_time_key"], bad_dep = time_key(df["departure_time"])
    stats["unparseable_time_values"] += int(bad_arr.sum() + bad_dep.sum())
    stats["no_scheduled_arrival(first stops)"] += int((df["sched_arr_time_key"] == -1).sum())
    stats["no_scheduled_departure(last stops)"] += int((df["sched_dep_time_key"] == -1).sum())

    # FIXING NULLS / BOOLEANS (make t/f = 0/1 flags)
    arr_c, arr_null, arr_invalid = to_flag(df["arrival_cancelled"])  
    dep_c, dep_null, dep_invalid = to_flag(df["departure_cancelled"])  
    full_c, full_null, full_invalid = to_flag(df["completely_cancelled"])
    part_c, part_null, part_invalid = to_flag(df["partly_cancelled"])
    plat, plat_null, plat_invalid = to_flag(df["platform_change"])
    stats["null_service_or_platform_flags_set_to_0"] += int(
        full_null.sum() + part_null.sum() + plat_null.sum())

    invalid_bool = arr_invalid | dep_invalid | full_invalid | part_invalid | plat_invalid
    stats["invalid_boolean_values"] += int(invalid_bool.sum())

    arr_d = pd.to_numeric(df["arrival_delay"], errors="coerce")
    dep_d = pd.to_numeric(df["departure_delay"], errors="coerce")
    stats["non_numeric_delay_values"] += int(
        (df["arrival_delay"].notna() & arr_d.isna()).sum()
        + (df["departure_delay"].notna() & dep_d.isna()).sum())
    if NULLIFY_DELAY_WHEN_CANCELLED:
        stats["delays_nullified_for_cancelled_stops"] += int(
            ((arr_c == 1) & arr_d.notna()).sum() + ((dep_c == 1) & dep_d.notna()).sum())
        arr_d = arr_d.mask(arr_c == 1)
        dep_d = dep_d.mask(dep_c == 1)
    out_of_range = (arr_d.abs() > SMALLINT_MAX) | (dep_d.abs() > SMALLINT_MAX)
    df["arrival_delay_min"] = arr_d.round().where(~out_of_range).astype("Int32")
    df["departure_delay_min"] = dep_d.round().where(~out_of_range).astype("Int32")

    # DERIVED ATTRIBUTE
    df["is_arrival_late"] = ((arr_d >= LATE_THRESHOLD_MIN) & (arr_c == 0)).astype("int8")

    df["arrival_cancelled"] = arr_c
    df["departure_cancelled"] = dep_c
    df["service_fully_cancelled"] = full_c
    df["service_partly_cancelled"] = part_c
    df["platform_changed"] = plat

    # MERGE surrogate keys from the dimensions
    df["station_key"] = df["station_code"].map(station_map)
    df = df.merge(train_df, how="left", on=["company", "service_type", "train_number"])

    reason = pd.Series("", index=df.index, dtype=object)

    def flag(mask, why):
        m = mask & (reason == "")
        stats[f"reject: {why}"] += int(m.sum())
        reason.loc[m] = why

    flag(df["service_rdt_id"].isna() | df["stop_rdt_id"].isna(), "missing service/stop id")
    flag(df["date_key"].isna() | ~df["date_key"].isin(date_keys), "invalid service date")
    flag(df["station_name"].isna(), "missing station name")
    flag(df["station_code"].isna(), "missing station code")
    flag(invalid_bool, "invalid Boolean flag value")
    flag(df["train_number"].isna(), "missing train number")
    flag(out_of_range.reindex(df.index, fill_value=False), "delay out of SMALLINT range")
    flag(df["station_key"].isna(), "station code not in dim_station")
    flag(df["train_key"].isna(), "train not in dim_train")
    still_ok = df[reason == ""]
    dup = pd.Series(False, index=df.index)
    dup.loc[still_ok.index] = still_ok.duplicated(subset="stop_rdt_id", keep="first")
    flag(dup, "duplicate stop id within chunk")  

    bad = reason != ""
    if bad.any():
        rej = raw.loc[bad].copy()
        rej["reject_reason"] = reason[bad].values
        rej.to_csv(REJECT_FILE, mode="a", header=not os.path.exists(REJECT_FILE), index=False)

    good = df.loc[~bad].copy()
    for c in ("date_key", "train_key", "station_key", "service_rdt_id", "stop_rdt_id"):
        good[c] = good[c].astype("int64")
    stats["sum_arrival_delay_loaded"] += int(good["arrival_delay_min"].fillna(0).sum())
    stats["sum_departure_delay_loaded"] += int(good["departure_delay_min"].fillna(0).sum())
    stats["late_arrivals_loaded"] += int(good["is_arrival_late"].sum())
    return good[FACT_COLS]


# =============================================================================
# VALIDATE
# =============================================================================


def validate(src_engine, dw, stats, limit, append):
    results = []

    def add(name, status, detail):
        results.append((name, status, detail))
        log.info("[%s] %s - %s", status, name, detail)

    with dw.cursor() as cur:
        cur.execute("SELECT COUNT(*), SUM(arrival_delay_min), SUM(departure_delay_min) "
                    "FROM fact_stop_event")
        fact_n, dw_arr, dw_dep = cur.fetchone()
        fact_n, dw_arr, dw_dep = int(fact_n), int(dw_arr or 0), int(dw_dep or 0)

        if append:
            add("row reconciliation", "SKIP", "append mode: fact table contains earlier rows")
        else:
            ok = stats["extracted"] == stats["loaded"] + stats["rejected"]
            add("rows extracted = loaded + rejected", "PASS" if ok else "FAIL",
                f"{stats['extracted']} = {stats['loaded']} + {stats['rejected']}")
            ok = fact_n == stats["loaded"]
            add("rows in fact table = rows loaded", "PASS" if ok else "FAIL",
                f"{fact_n} vs {stats['loaded']}")
            ok = (dw_arr == stats["sum_arrival_delay_loaded"]
                  and dw_dep == stats["sum_departure_delay_loaded"])
            add("delay totals in warehouse = totals transformed", "PASS" if ok else "FAIL",
                f"arrival {dw_arr} vs {stats['sum_arrival_delay_loaded']}; "
                f"departure {dw_dep} vs {stats['sum_departure_delay_loaded']}")

        if limit is None:
            with src_engine.connect() as conn:
                r = conn.execute(text(
                    f"SELECT COUNT(*), SUM(`{SRC_COLS['arrival_delay']}`) "
                    f"FROM `{SRC_TABLE}`")).fetchone()
            src_n, src_arr = int(r[0]), int(r[1] or 0)
            ok = src_n == stats["extracted"]
            add("source row count = rows extracted", "PASS" if ok else "FAIL",
                f"{src_n} vs {stats['extracted']}")
            add("source vs fact arrival-delay total", "INFO",
                f"source {src_arr}, fact {dw_arr}; differences are expected from "
                f"rejected rows and the cancelled-stop rule")

        for fk, dim, pk in (("date_key", "dim_date", "date_key"),
                            ("train_key", "dim_train", "train_key"),
                            ("station_key", "dim_station", "station_key"),
                            ("sched_arr_time_key", "dim_time", "time_key"),
                            ("sched_dep_time_key", "dim_time", "time_key")):
            cur.execute(f"SELECT COUNT(*) FROM fact_stop_event f "
                        f"LEFT JOIN {dim} d ON f.{fk} = d.{pk} WHERE d.{pk} IS NULL")
            n = cur.fetchone()[0]
            add(f"orphan check {fk} -> {dim}", "PASS" if n == 0 else "FAIL", f"{n} orphans")

        cur.execute("SELECT COUNT(*) - COUNT(DISTINCT stop_rdt_id) FROM fact_stop_event")
        n = cur.fetchone()[0]
        add("duplicate stop_rdt_id in fact", "PASS" if n == 0 else "FAIL", f"{n} duplicates")

        cur.execute("SELECT MIN(arrival_delay_min), MAX(arrival_delay_min), "
                    "AVG(is_arrival_late) FROM fact_stop_event")
        mn, mx, late = cur.fetchone()
        add("arrival delay range / late share", "INFO",
            f"min {mn}, max {mx} minutes; late share {float(late or 0):.2%} "
            f"(threshold {LATE_THRESHOLD_MIN} min)")
    return results


# =============================================================================
# MAIN
# =============================================================================


def parse_args():
    p = argparse.ArgumentParser(description="ETL: railway_source -> railway_dw")
    p.add_argument("--limit", type=int, default=None,
                   help="only process the first N source rows (testing)")
    p.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    p.add_argument("--append", action="store_true",
                   help="do not truncate fact_stop_event before loading")
    p.add_argument("--skip-validate", action="store_true")
    return p.parse_args()


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    root = logging.getLogger("etl")
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, mode="w")):
        h.setFormatter(fmt)
        root.addHandler(h)


def main():
    args = parse_args()
    setup_logging()

    if SRC["password"] is None:
        SRC["password"] = getpass.getpass(f"MySQL password for {SRC['user']}@{SRC['host']}: ")
    if DW["password"] is None:
        same = (DW["host"], DW["user"], DW["port"]) == (SRC["host"], SRC["user"], SRC["port"])
        DW["password"] = SRC["password"] if same else getpass.getpass(
            f"MySQL password for {DW['user']}@{DW['host']} (warehouse): ")

    if os.path.exists(REJECT_FILE):
        os.remove(REJECT_FILE)

    t0 = time.time()
    stats = Counter()
    src_engine = create_engine(URL.create(
        "mysql+pymysql", username=SRC["user"], password=SRC["password"], host=SRC["host"],
        port=SRC["port"], database=SRC["database"], query={"charset": "utf8mb4"}))
    dw = pymysql.connect(host=DW["host"], port=DW["port"], user=DW["user"],
                         password=DW["password"], database=DW["database"],
                         charset="utf8mb4", autocommit=False)
    try:
        with dw.cursor() as cur:
            check_schema(cur)

            # dimension 
            log.info("Loading dimensions ...")
            with src_engine.connect() as sconn:
                date_keys = load_dim_date(sconn, cur)
                load_dim_time(cur)
                station_map = load_dim_station(sconn, cur, stats)
                train_df = load_dim_train(sconn, cur)
            dw.commit()

            # fact
            if not args.append:
                cur.execute("TRUNCATE TABLE fact_stop_event")
                log.info("fact_stop_event truncated (full reload)")
            cur.execute("SET SESSION foreign_key_checks = 0")   # re-checked by validate()

            query = f"SELECT {select_list(FACT_SRC_COLS)} FROM `{SRC_TABLE}`"
            if args.limit:
                query += f" LIMIT {int(args.limit)}"
            log.info("Streaming source rows in chunks of %s ...", args.chunk_size)

            with src_engine.connect().execution_options(stream_results=True) as conn:
                for i, chunk in enumerate(
                        pd.read_sql_query(text(query), conn, chunksize=args.chunk_size), 1):
                    good = transform_chunk(chunk, station_map, train_df, date_keys, stats)
                    if len(good):
                        bulk_insert(cur, "fact_stop_event", FACT_COLS, to_rows(good, FACT_COLS))
                    dw.commit()
                    stats["loaded"] += len(good)
                    stats["rejected"] = stats["extracted"] - stats["loaded"]
                    log.info("chunk %s: extracted %s | loaded %s | rejected %s | %.0fs elapsed",
                             i, stats["extracted"], stats["loaded"], stats["rejected"],
                             time.time() - t0)

            cur.execute("SET SESSION foreign_key_checks = 1")

        # validation
        results = []
        if not args.skip_validate:
            log.info("Validating ...")
            results = validate(src_engine, dw, stats, args.limit, args.append)
    finally:
        dw.close()

    log.info("---------------- SUMMARY ----------------")
    for k in sorted(stats):
        log.info("%-48s %s", k, f"{stats[k]:,}")
    log.info("Reject details: %s | log: %s | total time %.1f min",
             REJECT_FILE, LOG_FILE, (time.time() - t0) / 60)
    if any(status == "FAIL" for _, status, _ in results):
        log.error("One or more validation checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
