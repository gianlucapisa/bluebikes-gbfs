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

# Time weights: each observation counts for the time until the station's next
# observation, capped at MAX_GAP so a hole in the data doesn't count as hours
# of "empty" or "full". Plain means would overweight periods with more snapshots.
MAX_GAP = 600  # seconds (2 x the 5-minute interval)
df = df.sort_values(["station_id", "snapshot_utc"])
nxt = df.groupby("station_id")["snapshot_utc"].shift(-1)
df["w"] = ((nxt - df["snapshot_utc"]).dt.total_seconds()
           .clip(upper=MAX_GAP).fillna(300))
df["w_empty"] = df["w"] * df["empty"]
df["w_full"] = df["w"] * df["full"]

tau = (df.groupby(["station_id", "dow", "hour"])
         .agg(n_snap=("empty", "size"),
              minutes_obs=("w", lambda s: s.sum() / 60),
              w=("w", "sum"), w_empty=("w_empty", "sum"), w_full=("w_full", "sum"))
         .reset_index())
tau["share_empty"] = tau["w_empty"] / tau["w"]
tau["share_full"] = tau["w_full"] / tau["w"]
tau = tau.drop(columns=["w", "w_empty", "w_full"])
tau["share_available"] = 1 - tau["share_empty"]
tau.to_csv(OUT / "tau_station_dow_hour.csv", index=False)
print(f"wrote {OUT / 'tau_station_dow_hour.csv'}")

# Gap check: how many snapshots per day did we actually get?
per_day = df.groupby(df["ts_local"].dt.date)["snapshot_utc"].nunique()
print("snapshots per day (target ~288):")
print(per_day.to_string())
