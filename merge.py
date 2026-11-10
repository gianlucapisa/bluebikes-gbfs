#!/usr/bin/env python3
"""
Merge all snapshots into one table and compute, per station x hour of day,
the share of time the station was empty / full (the tau needed for the
censored-demand correction).

    pip install pandas pyarrow
    python merge.py            # reads data/, writes out/
"""
from pathlib import Path

import pandas as pd

DATA = Path("data")
OUT = Path("out")
OUT.mkdir(exist_ok=True)

files = sorted((DATA / "status").glob("*/*.csv.gz"))
df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)

# Clean-up
df["snapshot_utc"] = pd.to_datetime(df["snapshot_utc"], utc=True)
df = df.drop_duplicates(["station_id", "feed_last_updated"])  # same feed state seen twice
df = df[(df["is_installed"] == 1)]                              # only active stations
df["ts_local"] = df["snapshot_utc"].dt.tz_convert("America/New_York")

try:
    df.to_parquet(OUT / "status_all.parquet", index=False)
except ImportError:  # no pyarrow installed -> compressed CSV
    df.to_csv(OUT / "status_all.csv.gz", index=False)
print(f"{len(files)} files, {len(df):,} rows, "
      f"{df['station_id'].nunique()} stations, "
      f"{df['ts_local'].min()} -> {df['ts_local'].max()}")

# Share of time empty / full by station x day of week x hour
df["dow"] = df["ts_local"].dt.dayofweek
df["hour"] = df["ts_local"].dt.hour
df["empty"] = (df["num_bikes_available"] == 0) | (df["is_renting"] == 0)
df["full"] = (df["num_docks_available"] == 0) | (df["is_returning"] == 0)

tau = (df.groupby(["station_id", "dow", "hour"])
         .agg(n_snap=("empty", "size"),
              share_empty=("empty", "mean"),
              share_full=("full", "mean"))
         .reset_index())
tau["share_available"] = 1 - tau["share_empty"]
tau.to_csv(OUT / "tau_station_dow_hour.csv", index=False)
print(f"wrote {OUT / 'tau_station_dow_hour.csv'}")

# Gap check: how many snapshots per day did we actually get?
per_day = df.groupby(df["ts_local"].dt.date)["snapshot_utc"].nunique()
print("snapshots per day (target ~288):")
print(per_day.to_string())
