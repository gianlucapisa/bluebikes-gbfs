# bluebikes-gbfs

Data pipeline for the Bluebikes rebalancing project: live station inventories every
5 minutes, monthly trip histories, and clean tables built from both, including a
ready-to-use van-routing instance for each night.

## Quick start

```bash
pip install -r requirements.txt
git pull                    # new snapshots arrive every hour, new trip months monthly
python build_all.py         # rebuilds out/ in a few seconds, no network needed
```

```python
import pandas as pd
inst = pd.read_parquet("out/instances/2026-11-20.parquet")    # one night, one row per station
dist = pd.read_parquet("out/distances.parquet")                 # station-to-station distances
D = dist.pivot(index="from_id", columns="to_id", values="minutes")   # square matrix if needed
```

`python build_all.py --date 2026-11-20` builds the instance for a specific morning.

## What runs automatically (GitHub Actions)

| Workflow | What it does | Output in the repo |
|---|---|---|
| `gbfs.yml` | Snapshot of every station every 5 minutes, committed hourly | `data/status/YYYY-MM-DD/HHMMSS.csv.gz` (UTC), `data/station_info/YYYY-MM-DD.csv.gz` |
| `trips.yml` | Checks days 3–14 of each month for the newly published month of trips | `data/trips/YYYYMM.parquet` (~12 MB per month) |

The first time, run **Actions → trips-monthly → Run workflow** to download the last
12 months (or set `since` to go further back; files before 202304 use a different
layout and are skipped).

## Scripts

| Script | Needs network | Writes |
|---|---|---|
| `logger.py` | yes (GBFS feed) | `data/status/`, `data/station_info/` |
| `fetch_trips.py` | yes (Bluebikes S3) | `data/trips/` |
| `build_stations.py` | no | `out/stations.parquet`, `out/distances.parquet` |
| `build_daily.py` | no | `out/night_inventory.parquet`, `out/hourly_flows.parquet`, `out/morning_flows.parquet`, `out/rebalancing.parquet` |
| `make_instance.py` | no | `out/instances/YYYY-MM-DD.parquet` / `.csv` |
| `merge.py` | no | `out/status_all.parquet`, `out/tau_station_dow_hour.csv` (share of time empty/full, for the censoring correction) |
| `build_all.py` | no | runs the three build scripts in order |

`out/` is not committed; rebuild it with `python build_all.py`.

## Output tables

All times are Boston local time. **Date D** means the night before and morning of D
(inventory at 02:00 on D, morning flows 06:00–10:00 on D).

**`stations.parquet`**: one row per station.
`station_id` (GBFS id, used in `data/status/`), `short_name` (code used in trip files,
e.g. `M32022`), `name`, `lat`, `lon`, `capacity`, `region_id`, `first_seen`,
`last_seen`, `active` (listed and installed right now), `in_gbfs` (False = only in trip
data, e.g. removed for winter; coordinates from trips, capacity unknown),
`trips_last_month`.

> **The two datasets use different station ids.** Trip files use `short_name`, GBFS uses
> `station_id`. Join through this table. 99.8% of trip ends match a GBFS station.

**`distances.parquet`**: every ordered pair of active stations.
`from_id`, `to_id`, `km_straight`, `km_road` (straight × 1.3), `minutes` (at 20 km/h).
These are rough estimates; replace them with real road times (e.g. OSRM) when needed.
The constants are at the top of `build_stations.py`.

**`night_inventory.parquet`**: per station per night, from our logs, at 00:00, 02:00
and 06:00. `bikes_HH` (available, incl. e-bikes), `ebikes_HH`, `disabled_HH`,
`docks_HH` (free docks), `snap_HH` (actual snapshot time, within 10 min of HH).

**`hourly_flows.parquet`**: `short_name`, `station_id`, `date`, `hour`, `pickups`,
`returns`. Only non-zero rows.

**`morning_flows.parquet`**: every station × every day, zeros filled: `pickups`,
`returns`, `net_outflow` (= pickups − returns) for 06:00–10:00, plus `dow` (0 = Mon),
`is_weekend`, `is_holiday` (US federal; Patriots' Day is not included).

**`rebalancing.parquet`**: change in bikes on site (incl. disabled) from 00:00 to
06:00, minus the change explained by trips. A large `unexplained` value means Bluebikes'
own vans moved bikes overnight, which gives benchmark (i), the current policy. This needs
logs and trips for the same nights: the logs start Nov 10, 2026, so the first nights
appear when November's trips are published in early December.

**`instances/YYYY-MM-DD.parquet`**: the input for the van-routing heuristic.

| column | meaning |
|---|---|
| `capacity` | usable docks tonight (bikes + free docks at 02:00) |
| `bikes` | available bikes at 02:00 |
| `forecast_net`, `spread` | mean and std of net morning outflow on this day type (weekday/weekend), last 8 weeks of trips |
| `lo`, `hi` | starting inventories that serve every net outflow in [f − s, f + s] |
| `target` | `bikes` clipped into [`lo`, `hi`]: stations already in range are left alone |
| `move` | `target − bikes`: > 0 drop off bikes, < 0 pick up, 0 skip |

The target is a **stand-in** that follows the same robust interval idea as the project
model. When the forecasting model and the LP are ready, replace `target` with the LP's
y_i; the rest of the instance stays the same. Note: the latest trip month lags the logs
by 3–6 weeks, so the forecast comes from earlier weeks (the script prints the gap).
