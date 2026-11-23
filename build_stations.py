#!/usr/bin/env python3
"""
Build the station lookup table and the station-to-station distance matrix.

    python build_stations.py

Reads   data/station_info/*.csv.gz   (daily GBFS station_information snapshots)
        data/trips/*.parquet         (optional, from fetch_trips.py)
Writes  out/stations.parquet
        out/distances.parquet

stations.parquet, one row per station:
    station_id    GBFS id (the id used in data/status/ snapshots)
    short_name    station code used in the trip files (e.g. "M32022")
    name, lat, lon, capacity, region_id
    first_seen, last_seen    dates the station appeared in our GBFS snapshots
    active        in the most recent station_information AND installed in the latest status snapshot
    in_gbfs       False = station only appears in trip data (e.g. removed for winter);
                  for those, lat/lon are the median of trip coordinates, capacity is unknown
    trips_last_month    pickups + returns in the most recent month of trip data

distances.parquet, one row per ordered pair of ACTIVE stations (i != j):
    from_id, to_id          GBFS station_id
    km_straight             great-circle distance
    km_road                 km_straight * ROAD_FACTOR (rough street-network detour)
    minutes                 km_road / VAN_KMH * 60
Swap in real road times (e.g. OSRM) later by replacing km_road / minutes.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data")
OUT = Path("out")
ROAD_FACTOR = 1.3   # typical ratio of street distance to straight-line distance in a city
VAN_KMH = 20.0      # average van speed in Boston traffic, incl. stops between stations


def load_station_info():
    files = sorted((DATA / "station_info").glob("*.csv.gz"))
    if not files:
        raise SystemExit("no files in data/station_info/")
    snaps = []
    for f in files:
        d = pd.read_csv(f, dtype={"station_id": str, "short_name": str, "legacy_id": str})
        d["snap_date"] = f.name[:10]
        snaps.append(d)
    allsnaps = pd.concat(snaps, ignore_index=True)
    latest_date = allsnaps["snap_date"].max()
    seen = allsnaps.groupby("station_id")["snap_date"].agg(first_seen="min", last_seen="max")
    # latest known attributes of every station ever seen
    latest = (allsnaps.sort_values("snap_date")
                      .drop_duplicates("station_id", keep="last")
                      .set_index("station_id"))
    st = latest[["short_name", "name", "lat", "lon", "capacity", "region_id"]].join(seen).reset_index()
    st["active"] = st["last_seen"] == latest_date
    # ...and installed according to the most recent status snapshot
    status_files = sorted((DATA / "status").glob("*/*.csv.gz"))
    if status_files:
        cur = pd.read_csv(status_files[-1], dtype={"station_id": str})
        installed = set(cur.loc[cur["is_installed"] == 1, "station_id"])
        st["active"] &= st["station_id"].isin(installed)
    st["in_gbfs"] = True
    st["first_seen"] = pd.to_datetime(st["first_seen"]).dt.date
    st["last_seen"] = pd.to_datetime(st["last_seen"]).dt.date
    return st


def add_trip_only_stations(st):
    files = sorted((DATA / "trips").glob("*.parquet"))
    st["trips_last_month"] = 0
    if not files:
        print("no trip data yet (run fetch_trips.py) - skipping trip station matching")
        return st
    cols = ["start_short", "end_short", "start_name", "end_name",
            "start_lat", "start_lng", "end_lat", "end_lng"]
    t = pd.read_parquet(files[-1], columns=cols)
    ends = pd.concat([
        t[["start_short", "start_name", "start_lat", "start_lng"]].set_axis(["short_name", "name", "lat", "lon"], axis=1),
        t[["end_short", "end_name", "end_lat", "end_lng"]].set_axis(["short_name", "name", "lat", "lon"], axis=1),
    ]).dropna(subset=["short_name"])
    ends["short_name"] = ends["short_name"].astype(str)
    counts = ends["short_name"].value_counts()
    st["trips_last_month"] = st["short_name"].map(counts).fillna(0).astype(int)

    unknown = ends[~ends["short_name"].isin(st["short_name"])]
    if len(unknown):
        extra = (unknown.groupby("short_name")
                        .agg(name=("name", lambda s: s.astype(str).mode().iat[0]),
                             lat=("lat", "median"), lon=("lon", "median"))
                        .reset_index())
        extra["station_id"] = "trip-only:" + extra["short_name"]
        extra["in_gbfs"] = False
        extra["active"] = False
        extra["trips_last_month"] = extra["short_name"].map(counts).astype(int)
        st = pd.concat([st, extra], ignore_index=True)
    share = counts[counts.index.isin(st.loc[st["in_gbfs"], "short_name"])].sum() / counts.sum()
    print(f"trip data {files[-1].stem}: {share:.1%} of trip ends are at stations in the GBFS feed; "
          f"{len(unknown['short_name'].unique()) if len(unknown) else 0} trip-only stations added")
    return st


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def build_distances(st):
    act = st[st["active"]].reset_index(drop=True)
    lat, lon = act["lat"].to_numpy(), act["lon"].to_numpy()
    km = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    i, j = np.where(~np.eye(len(act), dtype=bool))
    d = pd.DataFrame({
        "from_id": act["station_id"].to_numpy()[i],
        "to_id": act["station_id"].to_numpy()[j],
        "km_straight": km[i, j].astype("float32"),
    })
    d["km_road"] = (d["km_straight"] * ROAD_FACTOR).astype("float32")
    d["minutes"] = (d["km_road"] / VAN_KMH * 60).astype("float32")
    return d


def main():
    OUT.mkdir(exist_ok=True)
    st = add_trip_only_stations(load_station_info())
    st = st.sort_values(["in_gbfs", "short_name"], ascending=[False, True], ignore_index=True)
    st = st[["station_id", "short_name", "name", "lat", "lon", "capacity", "region_id",
             "first_seen", "last_seen", "active", "in_gbfs", "trips_last_month"]]
    st.to_parquet(OUT / "stations.parquet", index=False)
    print(f"wrote out/stations.parquet: {len(st)} stations "
          f"({st['active'].sum()} active, {(~st['in_gbfs']).sum()} trip-only)")

    d = build_distances(st)
    d.to_parquet(OUT / "distances.parquet", index=False)
    print(f"wrote out/distances.parquet: {len(d):,} pairs between {st['active'].sum()} active stations")


if __name__ == "__main__":
    main()
