"""Download MTA NYCT Paratransit open data and build a local DuckDB database.

Source: data.ny.gov (Socrata). The raw datasets are hourly; we aggregate them
server-side with SoQL so the local copy stays small (a few MB of Parquet).

Run:  uv run python -m transit_sql.data
"""

from pathlib import Path

import duckdb
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "paratransit.duckdb"

# Frozen analysis window, so evaluation results are reproducible.
DATA_END = "2026-09-30"

PAGE = 50_000

# table name -> list of (dataset id, SoQL query). Older and newer datasets are
# unioned into one table.
SOURCES = {
    "trips_daily": [
        (ds, f"""SELECT day AS service_date, provider_type, pickup_borough AS borough,
                 sum(trips) AS trips, sum(ridership) AS riders
                 WHERE day <= '{DATA_END}'
                 GROUP BY day, provider_type, pickup_borough""")
        for ds in ("fn46-66ir", "hasd-hdb7")
    ],
    "pickup_otp_daily": [
        (ds, f"""SELECT date AS service_date, provider_type, standard,
                 pickup_location AS borough,
                 sum(total_pickups) AS total_pickups,
                 sum(pickups_within_standards) AS ontime_pickups
                 WHERE date <= '{DATA_END}'
                 GROUP BY date, provider_type, standard, pickup_location""")
        for ds in ("rube-svnv", "dqvw-iu8s")
    ],
    "complaints_daily": [
        (ds, f"""SELECT date AS opened_date, provider_type, category,
                 sum(complaints::number) AS complaints
                 WHERE date <= '{DATA_END}'
                 GROUP BY date, provider_type, category""")
        for ds in ("98mr-pebg", "re4e-8rv8")
    ],
    "ride_time_monthly": [
        ("wtm4-vjm7", """SELECT date_trunc_ym(trip_date) AS month, provider_type,
                 peak_hour, origin AS origin_borough, within_max_ride_time,
                 sum(total_trips) AS trips, sum(total_ride_time_min) AS ride_time_min
                 GROUP BY date_trunc_ym(trip_date), provider_type, peak_hour,
                          origin, within_max_ride_time"""),
    ],
    "provider_no_shows_monthly": [
        ("47iu-idvc", """SELECT month, provider_type,
                 provider_no_shows AS no_shows_per_1000"""),
    ],
}

NUMERIC = {"trips", "riders", "total_pickups", "ontime_pickups", "complaints",
           "ride_time_min", "no_shows_per_1000"}
DATES = {"service_date", "opened_date", "month"}


def fetch(dataset_id: str, soql: str) -> pd.DataFrame:
    """Page through a SoQL query and return all rows."""
    query = " ".join(soql.split())
    # Paging needs a total order, or rows get skipped/duplicated between pages.
    order = query.split("GROUP BY ")[1] if "GROUP BY " in query else ":id"
    frames, offset = [], 0
    while True:
        resp = requests.get(
            f"https://data.ny.gov/resource/{dataset_id}.json",
            params={"$query": f"{query} ORDER BY {order} LIMIT {PAGE} OFFSET {offset}"},
            timeout=120,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        if len(rows) < PAGE:
            break
        offset += PAGE
    return pd.concat(frames, ignore_index=True)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in NUMERIC:
            df[col] = pd.to_numeric(df[col])
        elif col in DATES:
            df[col] = pd.to_datetime(df[col]).dt.date
        elif col == "within_max_ride_time":
            df[col] = df[col].str.upper() == "TRUE"
        else:
            df[col] = df[col].fillna("Unknown").str.strip()
    return df


def download() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for table, parts in SOURCES.items():
        if (RAW_DIR / f"{table}.parquet").exists():
            continue  # delete the file to re-download
        df = pd.concat([fetch(ds, q) for ds, q in parts], ignore_index=True)
        df = clean(df)
        df.to_parquet(RAW_DIR / f"{table}.parquet", index=False)
        print(f"{table}: {len(df):,} rows")


def build_db(db_path: Path = DB_PATH) -> None:
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    for table in SOURCES:
        con.execute(
            f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",
            [str(RAW_DIR / f"{table}.parquet")],
        )
    con.close()
    print(f"Built {db_path}")


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        build_db()
    con = duckdb.connect(str(DB_PATH), read_only=read_only)
    con.execute("SET enable_external_access = false")  # LLM SQL can't read files
    return con


if __name__ == "__main__":
    download()
    build_db()
