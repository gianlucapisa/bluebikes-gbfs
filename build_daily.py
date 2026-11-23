#!/usr/bin/env python3
"""
Turn the raw logs and trip files into per-station, per-day tables.

    python build_daily.py          # run build_stations.py first

Reads   out/stations.parquet, data/status/*/*.csv.gz, data/trips/*.parquet
Writes  out/night_inventory.parquet   from our GBFS logs
        out/hourly_flows.parquet      from trip data
        out/morning_flows.parquet     from trip data
        out/rebalancing.parquet       needs both for the same nights

All dates and hours are Boston local time. "date" D means the night before /
morning of D: inventory at 02:00 on D, morning flows 06:00-10:00 on D.

night_inventory: station_id, short_name, date, capacity, and for each hour HH in
    NIGHT_HOURS: bikes_HH (available, incl. e-bikes), ebikes_HH, disabled_HH,
    docks_HH (available docks), snap_HH (actual snapshot time used).
hourly_flows (only non-zero rows): short_name, station_id, date, hour, pickups, returns
morning_flows (every station x every day, zeros filled): short_name, station_id, date,
    dow (0=Mon), is_weekend, is_holiday, pickups, returns, net_outflow (= pickups - returns)
rebalancing: station_id, short_name, date, total_00, total_06 (bikes on site incl.
    disabled), trip_net_in (returns - pickups 00-06), unexplained (= change - trip_net_in).
    Large |unexplained| = bikes added/removed by Bluebikes' own vans overnight.
"""
from pathlib import Path

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

DATA = Path("data")
OUT = Path("out")
TZ = "America/New_York"
NIGHT_HOURS = [0, 2, 6]          # local hours at which to read overnight inventory
TOLERANCE_MIN = 10               # use a snapshot only if within this many minutes of the target
MORNING = (6, 10)                # morning window [start, end) in local hours
REBAL_WINDOW = (0, 6)            # overnight window for rebalancing detection


def status_file_index():
    rows = []
    for f in (DATA / "status").glob("*/*.csv.gz"):
        ts = pd.Timestamp(f"{f.parent.name}T{f.name[:2]}:{f.name[2:4]}:{f.name[4:6]}Z")
        rows.append((f, ts))
    idx = pd.DataFrame(rows, columns=["path", "utc"])
    idx["local"] = idx["utc"].dt.tz_convert(TZ)
    return idx.sort_values("utc", ignore_index=True)


def night_inventory(stations):
    idx = status_file_index()
    if idx.empty:
        return pd.DataFrame()
    dates = sorted(idx["local"].dt.date.unique())
    wanted = []  # (date, hour, path, snapshot time)
    for d in dates:
        for h in NIGHT_HOURS:
            target = pd.Timestamp(d).replace(hour=h).tz_localize(TZ, nonexistent="shift_forward", ambiguous=True)
            gap = (idx["local"] - target).abs()
            k = gap.idxmin()
            if gap[k] <= pd.Timedelta(minutes=TOLERANCE_MIN):
                wanted.append((d, h, idx.at[k, "path"], idx.at[k, "local"]))

    parts = []
    for d, h, path, ts in wanted:
        s = pd.read_csv(path, dtype={"station_id": str})
        s = s[s["is_installed"] == 1]
        parts.append(pd.DataFrame({
            "station_id": s["station_id"], "date": d, "hour": h, "snap": ts,
            "bikes": s["num_bikes_available"], "ebikes": s["num_ebikes_available"],
            "disabled": s["num_bikes_disabled"], "docks": s["num_docks_available"],
        }))
    long = pd.concat(parts, ignore_index=True)
    wide = long.pivot_table(index=["station_id", "date"], columns="hour",
                            values=["bikes", "ebikes", "disabled", "docks", "snap"], aggfunc="first")
    wide.columns = [f"{v}_{h:02d}" for v, h in wide.columns]
    wide = wide.reset_index()
    order = ["station_id", "date"] + [f"{v}_{h:02d}" for h in NIGHT_HOURS
                                      for v in ("bikes", "ebikes", "disabled", "docks", "snap")]
    wide = wide[[c for c in order if c in wide.columns]]
    wide = stations[["station_id", "short_name", "capacity"]].merge(wide, on="station_id", how="right")
    for c in wide.columns:
        if c.split("_")[0] in ("bikes", "ebikes", "disabled", "docks"):
            wide[c] = wide[c].astype("Int16")
    return wide.sort_values(["date", "short_name"], ignore_index=True)


def load_trips():
    files = sorted((DATA / "trips").glob("*.parquet"))
    if not files:
        return None
    t = pd.concat((pd.read_parquet(f, columns=["started_at", "ended_at", "start_short", "end_short"])
                   for f in files), ignore_index=True)
    for c in ("start_short", "end_short"):
        t[c] = t[c].astype("string")
    # the dates the trip files cover: whole calendar months, e.g. 202609..202610
    months = sorted(f.stem for f in files)
    first = pd.Timestamp(months[0] + "01").date()
    last = (pd.Timestamp(months[-1] + "01") + pd.offsets.MonthEnd(0)).date()
    return t, (first, last)


def hourly_flows(trips, covered, stations):
    pick = (trips.dropna(subset=["start_short"])
                 .assign(date=lambda x: x["started_at"].dt.date, hour=lambda x: x["started_at"].dt.hour)
                 .groupby(["start_short", "date", "hour"]).size().rename("pickups"))
    ret = (trips.dropna(subset=["end_short", "ended_at"])
                .assign(date=lambda x: x["ended_at"].dt.date, hour=lambda x: x["ended_at"].dt.hour)
                .groupby(["end_short", "date", "hour"]).size().rename("returns"))
    pick.index.names = ret.index.names = ["short_name", "date", "hour"]
    h = pd.concat([pick, ret], axis=1).fillna(0).astype("int32").reset_index()
    h = h.merge(stations[["short_name", "station_id"]], on="short_name", how="left")
    # keep only days inside the months we have (trips from late-night rides that cross
    # into a neighbouring month would otherwise create stray partial days)
    first, last = covered
    h = h[(h["date"] >= first) & (h["date"] <= last)]
    return h[["short_name", "station_id", "date", "hour", "pickups", "returns"]].sort_values(
        ["date", "hour", "short_name"], ignore_index=True)


def morning_flows(hourly, stations):
    m = hourly[(hourly["hour"] >= MORNING[0]) & (hourly["hour"] < MORNING[1])]
    m = m.groupby(["short_name", "date"])[["pickups", "returns"]].sum()
    names = pd.Index(sorted(set(stations["short_name"].dropna()) | set(hourly["short_name"])), name="short_name")
    dates = pd.Index(sorted(hourly["date"].unique()), name="date")
    full = pd.MultiIndex.from_product([names, dates])
    m = m.reindex(full, fill_value=0).reset_index()
    m["net_outflow"] = m["pickups"] - m["returns"]
    dt = pd.to_datetime(m["date"])
    m["dow"] = dt.dt.dayofweek
    m["is_weekend"] = m["dow"] >= 5
    hol = USFederalHolidayCalendar().holidays(dt.min(), dt.max())
    m["is_holiday"] = dt.isin(hol)
    m = m.merge(stations[["short_name", "station_id"]], on="short_name", how="left")
    return m[["short_name", "station_id", "date", "dow", "is_weekend", "is_holiday",
              "pickups", "returns", "net_outflow"]]


def rebalancing(night, hourly):
    a, b = REBAL_WINDOW
    if f"bikes_{a:02d}" not in night or f"bikes_{b:02d}" not in night:
        return pd.DataFrame()
    r = night[["station_id", "short_name", "date"]].copy()
    r[f"total_{a:02d}"] = night[f"bikes_{a:02d}"] + night[f"disabled_{a:02d}"]
    r[f"total_{b:02d}"] = night[f"bikes_{b:02d}"] + night[f"disabled_{b:02d}"]
    w = hourly[(hourly["hour"] >= a) & (hourly["hour"] < b)]
    flows = w.groupby(["short_name", "date"])[["pickups", "returns"]].sum()
    flows["trip_net_in"] = flows["returns"] - flows["pickups"]
    trip_dates = set(hourly["date"])
    r = r[r["date"].isin(trip_dates)]
    r = r.merge(flows["trip_net_in"].reset_index(), on=["short_name", "date"], how="left")
    r["trip_net_in"] = r["trip_net_in"].fillna(0).astype("Int16")
    r["unexplained"] = r[f"total_{b:02d}"] - r[f"total_{a:02d}"] - r["trip_net_in"]
    return r.dropna(subset=["unexplained"]).reset_index(drop=True)


def save(df, name):
    df.to_parquet(OUT / name, index=False)
    extra = ""
    if "date" in df and len(df):
        extra = f", {df['date'].min()} -> {df['date'].max()}"
    print(f"wrote out/{name}: {len(df):,} rows{extra}")


def main():
    st_path = OUT / "stations.parquet"
    if not st_path.exists():
        raise SystemExit("run build_stations.py first")
    stations = pd.read_parquet(st_path)

    night = night_inventory(stations)
    save(night, "night_inventory.parquet")

    loaded = load_trips()
    if loaded is None:
        print("no trip data yet (run fetch_trips.py) - skipping flow tables")
        return
    trips, covered = loaded
    hourly = hourly_flows(trips, covered, stations)
    save(hourly, "hourly_flows.parquet")
    save(morning_flows(hourly, stations), "morning_flows.parquet")

    rb = rebalancing(night, hourly)
    if rb.empty:
        print("rebalancing: no nights covered by both GBFS logs and trip data yet "
              "(logs start Nov 10, 2026; that month's trips are published in early December)")
    else:
        save(rb, "rebalancing.parquet")


if __name__ == "__main__":
    main()
