#!/usr/bin/env python3
"""
Build a van-routing instance for one night: where every station stands at 02:00,
where it should be by morning, and how many bikes to add or remove.

    python make_instance.py                    # latest night we have logs for
    python make_instance.py --date 2026-11-20
    python make_instance.py --date 2026-11-20 --weeks 6

Reads   out/stations.parquet, out/night_inventory.parquet, out/morning_flows.parquet
Writes  out/instances/YYYY-MM-DD.parquet  (+ .csv), one row per station:

    station_id, short_name, name, lat, lon
    capacity        docks usable tonight (bikes_02 + docks_02)
    bikes           available bikes at 02:00
    forecast_net    f: expected net morning outflow 06-10 (pickups - returns)
    spread          s: std of the net morning outflow on that day type
    lo, hi          range of starting inventories that serves every outflow in [f - s, f + s]
    target          bikes we want at the start of the morning
    move            target - bikes   (> 0: drop off bikes, < 0: pick up bikes, 0: leave alone)

STAND-IN TARGET, a simplified version of the robust model in the project write-up.
Until the forecasting model and LP exist, f and s are the mean and std of net morning
outflow on the same kind of day (weekday / weekend, no holidays) over the last --weeks
weeks of trip data. Net outflow may be anywhere in [f - s, f + s], so the station needs
    >= f + s bikes          (enough for the worst-case outflow) and
    >= -(f - s) free docks  (enough for the worst-case inflow)
-> lo = max(0, f + s), hi = min(cap, cap + f - s). The target is the current inventory
clipped into [lo, hi]: stations already inside the range are left alone. If the range is
empty (too uncertain for the station's size) the target is its midpoint.
Replace the target column with the LP's y_i when it is ready; nothing else changes.
Distances between stations: out/distances.parquet.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("out")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="morning date YYYY-MM-DD (default: latest in the logs)")
    ap.add_argument("--weeks", type=int, default=8, help="weeks of trip history for the forecast")
    a = ap.parse_args()

    stations = pd.read_parquet(OUT / "stations.parquet")
    night = pd.read_parquet(OUT / "night_inventory.parquet")
    flows = pd.read_parquet(OUT / "morning_flows.parquet")

    day = (pd.Timestamp(a.date).date() if a.date
           else night.loc[night["bikes_02"].notna(), "date"].max())
    tonight = night[(night["date"] == day) & night["bikes_02"].notna()]
    if tonight.empty:
        raise SystemExit(f"no 02:00 inventory for {day}; available: "
                         f"{night['date'].min()} -> {night['date'].max()}")

    # forecast: mean net outflow on the same day type, last N weeks of trip data
    weekend = pd.Timestamp(day).dayofweek >= 5
    hist = flows[(flows["is_weekend"] == weekend) & ~flows["is_holiday"]]
    cutoff = hist["date"].max() - pd.Timedelta(weeks=a.weeks)
    hist = hist[hist["date"] > cutoff]
    fc = (hist.groupby("short_name")["net_outflow"]
              .agg(forecast_net="mean", spread="std").fillna(0.0))
    gap_days = (pd.Timestamp(day) - pd.Timestamp(hist["date"].max())).days

    inst = tonight[["station_id", "short_name", "bikes_02", "docks_02"]].merge(
        stations[["station_id", "name", "lat", "lon"]], on="station_id", how="left")
    inst["capacity"] = (inst["bikes_02"] + inst["docks_02"]).astype(int)
    inst["bikes"] = inst["bikes_02"].astype(int)
    inst = inst.merge(fc, left_on="short_name", right_index=True, how="left")
    new = inst["forecast_net"].isna()          # stations that opened after the trip data
    inst[["forecast_net", "spread"]] = inst[["forecast_net", "spread"]].fillna(0.0)
    f, sd, cap = inst["forecast_net"], inst["spread"], inst["capacity"]
    lo = np.ceil(np.maximum(0, f + sd))
    hi = np.floor(np.minimum(cap, cap + f - sd))
    empty = lo > hi
    mid = np.clip(np.round((cap + f) / 2), 0, cap)
    inst["lo"] = np.where(empty, mid, lo).astype(int)
    inst["hi"] = np.where(empty, mid, hi).astype(int)
    inst["target"] = np.clip(inst["bikes"], inst["lo"], inst["hi"]).astype(int)
    inst["move"] = inst["target"] - inst["bikes"]
    for c in ("forecast_net", "spread"):
        inst[c] = inst[c].round(2)
    inst = inst[["station_id", "short_name", "name", "lat", "lon", "capacity", "bikes",
                 "forecast_net", "spread", "lo", "hi", "target", "move"]]
    inst = inst.sort_values("move", ignore_index=True)

    out_dir = OUT / "instances"
    out_dir.mkdir(exist_ok=True)
    inst.to_parquet(out_dir / f"{day}.parquet", index=False)
    inst.to_csv(out_dir / f"{day}.csv", index=False)

    print(f"instance for the morning of {day} ({'weekend' if weekend else 'weekday'}), "
          f"{len(inst)} stations -> out/instances/{day}.parquet")
    print(f"forecast from {hist['date'].min()} -> {hist['date'].max()} "
          f"({gap_days} days before this night; seasonal mismatch is likely if this is large)")
    print(f"bikes to pick up: {-inst.loc[inst['move'] < 0, 'move'].sum()}, "
          f"to drop off: {inst.loc[inst['move'] > 0, 'move'].sum()}, "
          f"stations to visit: {(inst['move'] != 0).sum()}, with |move| >= 5: {(inst['move'].abs() >= 5).sum()}")
    if new.any():
        print(f"{new.sum()} stations have no trip history; treated as forecast 0")


if __name__ == "__main__":
    main()
