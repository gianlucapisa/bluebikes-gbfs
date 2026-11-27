# PLAN: Bluebikes van rebalancing (MIT 15.C57 final project)

Gianluca Pisa, James Wreh. Written 2026-11-27 at the end of the planning session.

**How to use this file.** It is the single reference for the project. Section 1 is the
project in a few lines. Section 3 lists every decision we made and why. Sections 4 to 6
explain how each piece works. **Section 11 is the checklist**: work top to bottom and tick
items as they are done. To tick an item, change `- [ ]` to `- [x]` and add `(done MM-DD, Name)`.
Anyone can pick any open item. If you change a decision, edit Section 3 and write the date.

---

## Contents
1. [The project in one page](#1-the-project-in-one-page)
2. [The data we have](#2-the-data-we-have)
3. [Decisions](#3-decisions)
4. [The method, step by step](#4-the-method-step-by-step)
5. [How we test it](#5-how-we-test-it)
6. [Parameters and sensitivity analysis](#6-parameters-and-sensitivity-analysis)
7. [What the data already showed us](#7-what-the-data-already-showed-us)
8. [Assumptions and limitations (goes in the report)](#8-assumptions-and-limitations)
9. [Upgrades and the "if we had another week" list](#9-upgrades-and-the-if-we-had-another-week-list)
10. [Code layout](#10-code-layout)
11. [CHECKLIST](#11-checklist)
12. [Report, slides and video outline](#12-report-slides-and-video-outline)
13. [Glossary](#13-glossary)

---

## 1. The project in one page

**Problem.** Bluebikes has about 600 stations. Commuting empties some stations every morning
(residential areas) and fills others (Kendall, the Financial District, Seaport). People then find
no bike to rent, or no dock to return to. Vans can move bikes around, at a cost.

**What we decide.** For each station *i*, how many bikes it should have when the morning
starts (`y_i`), and the van routes that get it there.

**Objective (from the interim report).**

```
minimize   van cost  +  Σ_i [ e · (failed pickups at i)  +  f · (failed returns at i) ]
```

with `e > f`. Someone who cannot get a bike does not ride at all. Someone who cannot return a
bike just rides to the next station.

**Pipeline.**
1. **Forecast** each station's net morning outflow (bikes taken minus bikes returned, 06:00–10:00).
2. **Robust LP:** choose target inventories `y_i` that minimize worst-case failures, allowing
   for forecast error.
3. **Routing heuristic:** plan the van routes that move bikes from surplus stations to deficit stations.
4. **Evaluate** on real November mornings against two benchmarks: what Bluebikes actually did,
   and a simple greedy rule.

**Scope of the first version.** One **night shift (00:00–06:00)** that prepares stations for
the **weekday morning peak (06:00–10:00)**. Extensions are listed in Section 9.

**Deadlines (from the professor).**

| Date | What |
|---|---|
| Oct 16 | Project registration (done) |
| Nov 06 | One-page interim report (done) |
| **Mon Dec 07** | **Slides + technical report (5–10 pages) + video (≤ 5 min)**, on Gradescope, one submission per team with both members added. The video link goes on page 1 of the report. |
| Dec 09–10 | In-class presentations (some projects) |

The report should answer: (i) which problem; (ii) why it matters; (iii) which data; (iv) how
we tackled it; (v) key findings; (vi) impact; (vii) what we would do with another week.

---

## 2. The data we have

Everything is in this repository. Run `git pull` to get the latest data. `python build_all.py`
rebuilds the clean tables in `out/` in a few seconds, with no internet needed.

### 2.1 Raw data (collected automatically by GitHub Actions)

| Source | What it is | Where | Coverage |
|---|---|---|---|
| **Our GBFS logs** (`logger.py`, workflow `gbfs.yml`) | Every station's state every 5 minutes: bikes available, e-bikes, disabled bikes, free docks, installed/renting/returning flags | `data/status/YYYY-MM-DD/HHMMSS.csv.gz` (times in UTC) | From **Nov 10, 2026**, ongoing. ~598 stations in the feed, ~584 installed |
| **Station information** (same logger, once a day) | Station name, `short_name` code, lat/lon, capacity, region | `data/station_info/YYYY-MM-DD.csv.gz` | From Nov 10, 2026 |
| **Trip histories** (`fetch_trips.py`, workflow `trips.yml`) | Every completed trip: start/end time, start/end station, bike type, member/casual | `data/trips/YYYYMM.parquet` | **Nov 2025 → Oct 2026** (12 months, ~4.6 M trips). Bluebikes publishes each month between the 4th and the 10th of the following month |

**Gaps in the logs** (the logger restarted or was being set up):
- Nov 10, 2026: several hours missing, including the morning (logs start properly ~07:40 Boston time).
- Nov 15/16, 2026: about 5.5 h missing (19:30 Sun → 01:00 Mon, Boston time). This does not
  affect the 02:00 or 06:00 readings.

**The two datasets use different station ids.** Trip files use `short_name` (e.g. `M32022`).
GBFS uses `station_id`. `out/stations.parquet` links them. 99.8% of trip ends match a GBFS station.

### 2.2 Clean tables built from the raw data (`python build_all.py`)

All times in these tables are Boston local time. "Date D" means the night before and the morning of D.

| Table | Built by | Contents |
|---|---|---|
| `out/stations.parquet` | `build_stations.py` | One row per station: ids, name, lat/lon, capacity, active flag, trips last month |
| `out/distances.parquet` | `build_stations.py` | Every pair of active stations: straight-line km, road km (= straight × 1.3), minutes at 20 km/h |
| `out/night_inventory.parquet` | `build_daily.py` | Per station per night: bikes, e-bikes, disabled bikes, free docks at 00:00, 02:00, 06:00 |
| `out/hourly_flows.parquet` | `build_daily.py` | Pickups and returns per station per hour (from trips) |
| `out/morning_flows.parquet` | `build_daily.py` | Pickups, returns, net outflow 06–10 per station per day, with day of week, weekend and holiday flags |
| `out/rebalancing.parquet` | `build_daily.py` | Overnight change in bikes not explained by trips (= van moves). **Will be almost empty**: see Section 7.2 |
| `out/instances/YYYY-MM-DD.parquet` | `make_instance.py` | One night's input for routing: capacity, bikes at 02:00, forecast, `lo`/`hi`, `target`, `move`. The current `target` is a **stand-in** (mean ± std of the last 8 weeks); the LP will replace it |
| `out/tau_station_dow_hour.csv` | `merge.py` (run separately) | Share of time each station was empty / full, by station × day of week × hour. Input for the censoring correction |

### 2.3 What is missing (we build it, see the checklist)
Weather forecasts, the school calendar flag, the per-station regression, the censoring
correction, the robust LP, routing, the greedy benchmark, the evaluator/replay, real road
distances (OSRM), and the experiment runner.

### 2.4 The central data problem: trips and logs never cover the same days
Trips end on **Oct 31**. Logs start on **Nov 10**. November's trips will be published around
**Dec 4–10**, probably too late. So **we plan as if no day has both**. Every choice below works
around this (Decisions D8, D9, D12).

---

## 3. Decisions

Each decision has the reason behind it. If you want to change one, change it here first and write the date.

| # | Decision | Why |
|---|---|---|
| D1 | **Build a simple end-to-end version first, then upgrade each stage.** The first version (stand-in forecast, LP, routing, greedy benchmark, evaluator) should be ready by Nov 29; upgrades follow | With 10 days left we always have a complete result to present. Risky parts can only make it better, never block it |
| D2 | **First version: one night shift (00–06) protecting the weekday morning peak (06–10).** Weekends out of scope | The morning commute is the biggest, most predictable imbalance, and at night inventories stay still while vans work, so routing is a fixed problem |
| D3 | **Upgrade A: rebalancing in two shifts**, night + midday (~12–15) protecting the evening (15–24), so the full weekday is covered. **Upgrade B: vans running close to Bluebikes' real ~20 h/day**, with the same pipeline re-run every few hours | Bluebikes' vans run all day (media kit: 6am–10pm; Crimson: ~20 h). Stations that need bikes in the morning and empty docks in the evening can only be fixed by daytime shifts. The code accepts a list of shifts from the start, so adding shifts later is a configuration change |
| D4 | Bluebikes really rebalances all day; **we model only the "prepare for the morning peak" job** and say so in the report | It's the cleanest piece of the problem we can model in the time we have. Fair comparison: see D16 |
| D5 | **City of Boston lost-trips data: forget it** | They did not reply |
| D6 | **Van parameters:** 4 vans, 20 bikes each, 00:00–06:00 shift, 20 km/h on road distance, 5 min per stop + 0.5 min per bike. Sensitivity on the fleet: {2, 4, 6, 8} | Based on the Bluebikes media kit (4–5 vans, 20–25 bikes). The fleet sweep gives a cost-vs-service curve ("cut cost or serve more riders") |
| D7 | **One repo, no fixed owners.** Whoever is free takes the next open checklist item | Team choice |
| D8 | **Learn from trips (Nov 2025 → Oct 2026); test on the weekday mornings we logged in November** | Testing needs 2am bike counts, which only our logs have. Testing on later days than all the training data is the cleanest out-of-sample setup |
| D9 | **Censoring correction: learn each station's typical empty/full pattern from the November logs and apply it to the earlier trip data**, capped at ×3, valets excluded. Upgrade, not in the first version. If November's trips arrive in time, redo it with matched days | It's the only way to correct for hidden demand without days that have both trips and logs. The first version without it gives a "before vs after" comparison |
| D10 | **Forecast: one OLS regression per station** on 06–10 net outflow. Inputs: rain (0/1), temperature, day-of-week dummies (Monday = baseline), school in session (0/1). Holidays excluded. No month variable. Small stations fall back to the 8-week average | Matches the interim report and is easy to explain. ~200 mornings per station is plenty for ~7 coefficients |
| D11 | **Uncertainty `s_i` = standard deviation of each station's forecast errors on October 2026**, from a model trained on Nov 2025 → Sep 2026. Beta standard errors are reported for interpretation only | The LP needs the error on a single morning's prediction, which mostly comes from residual noise, not from uncertainty in the betas. Measuring it on unseen days makes it honest |
| D12 | **Robust interval `[f_i − k·s_i, f_i + k·s_i]`, candidates `k ∈ {1, 2}`, chosen on the tuning mornings.** `k = 0` reported as a comparison line only | Choosing on the test days would be cheating. `k = 0` shows whether being robust actually helps |
| D13 | **Costs: `e = 3`, `f = 1`, `λ = 1` ($ per failed pickup / failed return / bike moved).** Sensitivity: `e/f ∈ {2, 3, 5}`, `λ ∈ {0.5, 1, 2}`. Headline numbers are **raw failure counts** | ~$3 ≈ one lost ride; ~$1 ≈ a detour; ~$1 per bike moved (Bluebikes rebalancing paper). Raw counts don't depend on our prices |
| D14 | **Greedy benchmark (ii):** rank by 8-week average net outflow, fill the top 10 outflow stations to capacity, empty the top 10 inflow stations to 0, routed with **the same heuristic and van budget** | The obvious rule an operator would use. Same routing means the only difference is which targets are chosen |
| D15 | **Routing:** one depot at the trip-weighted centre of the stations; vans start and end there **empty**; drive time counts in the 6 h. **All vans are routed at the same time** (the van that is free soonest picks next, randomly among the 3 closest useful stations). 1,000 full fleet plans, keep the best total score. Replay starts from what the vans **actually reached**. Upgrades: local search, second depot location | Planning vans one by one is greedy: van 1 takes the best stations. No public depot address exists |
| D16 | **Benchmark (i), Bluebikes' policy = the inventories we observed at 06:00.** All policies are replayed through the same 06–10 morning with no rebalancing during that window | Fair, like-for-like: "who sets the stations up better for the peak?" |
| D17 | **Replay: steps of 8 or more bikes in 5 minutes are van moves and are removed** from the flows. Re-check against November's trips if they arrive | Data analysis, Section 7.1: below 8, a third to a half of the jumps are real rider rushes; at 8 and above the riders' share stops falling |
| D18 | **Valet stations are excluded everywhere** (forecast, LP, routing, test). Rule: median weekday 06–10 net inflow > capacity in the October trips. Result: **Ames St at Main St** and **Longwood Ave at Binney St** | Staff handle the overflow, so the feed is wrong there. Bluebikes' valet page lists exactly these two (Mon–Fri 7am–7pm) |
| D19 | **Weather: Open-Meteo Historical Forecast API**, one central Boston point. `rain = 1` if forecast 06–10 precipitation ≥ 1 mm; `temp` = mean forecast 06–10 temperature (°C) | Uses forecasts, not what actually happened, so no look-ahead bias. Free, no API key |
| D20 | **Distances: straight-line × 1.3 for the first version; real OSRM road times as an early upgrade** | The bridges over the Charles matter for routing, and the upgrade is cheap (download once, save in the repo) |
| D21 | **School flag:** 1 on weekdays during term: Fall 2025 (→ Dec 19, 2025), Spring 2026 (Jan 20 → May 15), Fall 2026 (Sep 2 → Dec 18); 0 during winter and summer breaks. Check against MIT/BU calendars | Students are a big share of Boston riders. A shared flag captures season better than month dummies with only 12 months of data |
| D22 | **Timeline:** first end-to-end version by Sun Nov 29, freeze Thu Dec 3, write Dec 4–6, submit Mon Dec 7. Upgrades are optional and dropped if late | Section 11 |
| D23 ⚠️ | **Add back hidden demand in the replay (in the first version)**: in 5-minute steps when the real station was empty (or full), add the pickups (or returns) that riders typically attempt at that station and hour. **Needs your confirmation**, see Section 5.4 | Without it, Bluebikes' policy scores ~0 failures by construction and benchmark (i) is meaningless |
| D24 | **Tools:** Python, pandas, `scipy.optimize.linprog` (HiGHS solver) for the LP. Add `scipy` to `requirements.txt` | Free, no licence, works with what we already use |

---

## 4. The method, step by step

### 4.0 Which days play which role

| Role | Days | Used for |
|---|---|---|
| **Training the forecast** | Weekdays Nov 1, 2025 → Sep 30, 2026 (trips) | Learn the regression coefficients |
| **Validating the forecast** | Weekdays in October 2026 (trips) | Measure forecast error → `s_i`. Compare the regression with the 8-week-average baseline |
| **Final forecast model** | Weekdays Nov 2025 → Oct 2026 | Retrain on everything to forecast the November mornings |
| **Tuning `k`** | **Nov 12, 13, 14** (logs) | Choose `k ∈ {1, 2}`. Nov 10 is excluded (morning missing from the logs) and Nov 11 (Veterans Day, a federal holiday) |
| **Testing** | **Nov 17–20, Nov 23–25, Nov 30, Dec 1–3** (logs), 11 mornings | Headline results. Thanksgiving (Nov 26) and Nov 27 excluded |

Only 3 tuning mornings is thin. It's enough to choose between 2 values of `k`, but we
state it. If the result is close, prefer `k = 1`.

### 4.1 Data preparation

1. **Station list:** active stations from `out/stations.parquet`, **minus the 2 valet stations**
   (`Ames St at Main St`, `Longwood Ave at Binney St`).
2. **Inventory and capacity per night:** from `night_inventory`:
   - `b_i` = bikes available at 02:00
   - `C_i` = usable capacity tonight = bikes available + free docks at 02:00 (broken docks don't count)
   - `obs06_i` = bikes available at 06:00 (for benchmark i and the replay)
3. **Morning flows from the logs (for the test replay):** for each station, the step-by-step
   change in `num_bikes_available` across the 48 five-minute steps from 06:00 to 10:00.
   Remove steps of 8 or more bikes in absolute value (van moves, D17) by setting them to 0.
   Keep a count of removed steps per morning; it goes in the report.
4. **Demand history from trips (for the forecast):** `out/morning_flows.parquet` gives 06–10
   pickups, returns and net outflow per station per day. Keep weekdays, drop holidays.

### 4.2 Forecast

**First version (already exists in `make_instance.py`):** `f_i` = mean, and `s_i` = standard
deviation, of net outflow 06–10 over the last 8 weeks of weekday trips. This is also the
**baseline** the regression must beat.

**Upgrade: OLS per station.** For station *i* on morning *t*:

```
net_outflow_it = β0 + β1·rain_t + β2·temp_t + β3·tue_t + β4·wed_t + β5·thu_t + β6·fri_t + β7·school_t + ε_it
```

- **Dummy variables** turn categories into 0/1 columns. `tue_t = 1` on Tuesdays and 0
  otherwise; Monday is the baseline (all four day columns = 0), so `β3` reads as "Tuesday vs
  Monday". One category is always left out; otherwise the model can't tell it apart from `β0`.
- `rain_t`, `temp_t` come from Open-Meteo **forecasts** (Section 4.3), never from observed weather.
- **Holidays** are removed from the data rather than added as a column (~10 per year is too few to learn from).
- **No month variable:** with 12 months each month appears once, so the model could not tell
  "October" from "October's weather". Temperature and the school flag capture season instead.
- **Small stations:** if a station has fewer than 60 training mornings, or median pickups + returns
  under 2 per morning, use the 8-week-average baseline instead.
- **Steps:**
  1. Fit on Nov 2025 → Sep 2026.
  2. Predict October 2026 and compute each station's errors.
     `s_i` = standard deviation of those errors.
  3. Report MAE/RMSE of the regression vs the 8-week baseline. The result reads "the forecast cuts error by X%".
  4. Refit on Nov 2025 → Oct 2026 and predict each November tuning/test morning, giving `f_i`.
  5. Save the coefficients and standard errors. Examples for the report: "rain lowers net
     outflow at residential station X by 3 bikes".

### 4.3 Weather and the school flag
- **Open-Meteo Historical Forecast API** (`historical-forecast-api.open-meteo.com`). One point
  in central Boston (≈ 42.36 N, 71.06 W), hourly `precipitation` and `temperature_2m`,
  Nov 1, 2025 → Dec 3, 2026. Timezone America/New_York.
- Per day: `rain = 1` if the 06–10 precipitation sum ≥ 1 mm; `temp` = mean 06–10 temperature.
- Save to `data/weather/forecast_daily.csv` (committed, so everyone has the same file).
- **Caveat for the report:** the archive stitches together the most recent model runs (roughly
  0–24 h ahead), not exactly "issued at 6pm the evening before". It's close enough; we state it.
- **School flag** (D21): a small table of term dates in `model/calendar.py`, checked against the
  MIT and BU academic calendars.

### 4.4 Censoring correction (upgrade, D9)
Problem: trip data only counts **completed** trips. When a station is empty, people who wanted a
bike leave without one, so the trips understate demand. Same for returns at a full station.

1. Run `merge.py` on the logs. It gives, for each station × day of week × hour, the share of
   time the station was **empty** (`share_empty`) and **full** (`share_full`). Use weekdays only
   (Nov 10 → latest) and average Mon–Fri if a single weekday is too noisy.
2. For each historical trip-hour at that station and hour:
   `corrected pickups = pickups / max(1 − share_empty, 1/3)`, and the same for returns with `share_full`.
   The `1/3` cap means demand is scaled by **at most ×3** (dividing by tiny shares is pure noise).
3. Rebuild morning net outflow from the corrected pickups and returns, then refit the forecast.
4. Report "before vs after": "ignoring hidden demand underestimates morning demand at the busiest stations by X%".
5. Assumption to state: **a station that tends to run empty in November behaved the same way in
   earlier months.** It's reasonable for commuter patterns, weaker in summer.
6. If November's trips arrive before Dec 7, redo it properly using matched days.

### 4.5 Robust LP (target inventories)

The uncertainty is in the **net outflow `d_i`** of each station. Instead of trusting `f_i`
exactly, we allow `d_i ∈ [f_i − k·s_i, f_i + k·s_i]`.

- Failed pickups if more bikes leave than the station holds: `max(0, d_i − y_i)`. The worst case
  is the **top** of the interval.
- Failed returns if more bikes arrive than there are free docks: `max(0, −d_i − (C_i − y_i))`.
  The worst case is the **bottom** of the interval.

Because each worst case sits at a known end of the interval, the problem becomes linear:

```
minimize    Σ_i ( e·u_i + f·v_i )  +  λ · ½ Σ_i w_i
subject to  u_i ≥ (f_i + k·s_i) − y_i          u_i ≥ 0      worst-case failed pickups
            v_i ≥ y_i − C_i − (f_i − k·s_i)    v_i ≥ 0      worst-case failed returns
            w_i ≥ y_i − b_i ,  w_i ≥ b_i − y_i              bikes moved at station i
            Σ_i y_i = Σ_i b_i                               vans move bikes, they don't create them
            ½ Σ_i w_i ≤ M                                   van budget: at most M bikes moved
            0 ≤ y_i ≤ C_i
```

- `½ Σ w_i` = number of bikes carried (each bike is counted once at pickup and once at drop-off).
- `y_i` can be fractional. Round to integers, then fix the total so `Σ y_i = Σ b_i`.
- **`M` (how many bikes the fleet can move in one shift)** is calibrated once. Run the routing
  heuristic with no limit on targets, see how many bikes 4 vans actually move in 6 h, and use that
  as `M`. Rescale it for 2/6/8 vans in the sweep. Re-calibrate when distances change (OSRM).
- Output: `y_i`, written into the instance's `target` column (replacing the stand-in), with `move = y_i − b_i`.
- Solver: `scipy.optimize.linprog(method="highs")`. About 580 stations × 4 variables, which solves in under a second.
- Simplification to state: the LP uses the **net** flow over the whole window, not the order of
  events. The replay (Section 5) uses the real 5-minute order, so the evaluation captures this.

### 4.6 Routing heuristic (simultaneous, randomized nearest neighbour)

**Inputs:** for each station, the bikes to add (`move > 0`) or remove (`move < 0`); the distance
matrix; the van parameters (Section 6); the depot.

**Depot:** the trip-weighted centre of the stations, i.e. the point that minimizes trip-weighted
distance to all stations. We compute it from the data and expect it around Back Bay/Kendall.
No public depot address exists, so we state the assumption.

**Building one fleet plan:**
1. All vans start at the depot at 00:00, empty.
2. Repeat: take **the van whose clock is earliest**. List the "useful" stations given what's
   **still needed now**: stations with bikes to remove if the van has room, stations needing
   bikes if the van carries some. **Pick randomly among the 3 closest.** Load or unload
   `min(what the station still needs, what the van can take or give)`. Move the van's clock forward
   by drive time + 5 min + 0.5 min × bikes.
3. A van stops when its next stop plus the drive back to the depot would exceed 06:00. It must
   unload everything before returning (to the nearest deficit station), so it returns **empty**.
4. Rules: no station below 0 bikes or above `C_i`; no van above 20 bikes. E-bikes and regular
   bikes are treated the same.

**Choosing a plan:** build **1,000 fleet plans** (different random seeds) and keep the best by
total score:

```
score = Σ_i [ e·(worst-case failed pickups avoided) + f·(worst-case failed returns avoided) ]  −  λ·(bikes moved)
```

using the same formulas as the LP, at the inventories the plan actually reaches.

**Output:** the routes (ordered stops, bikes loaded/unloaded, times) and `reached_i`, the
inventory each station ends up with. **The test starts from `reached_i`, not from the LP's ideal `y_i`.**
Report "share of the LP's plan the vans delivered".

**Upgrades:** local search (swap stops between vans, reverse a segment, keep improvements); second depot location.

### 4.7 Benchmarks
- **(i) Bluebikes' actual policy:** starting inventory = `obs06_i`, the bike counts we logged at 06:00.
  Section 7.2 shows their vans essentially don't work at night, so this is "the system after no
  overnight rebalancing". Our night shift is a different way of operating, not a copy of theirs.
- **(ii) Greedy:** rank stations by the 8-week-average net outflow. Targets: the 10 with the
  largest outflow → full (`C_i`), the 10 with the largest inflow → 0 bikes, everything else
  unchanged. The bike total must balance: if the bikes removed from the 10 don't fill the other
  10, fill the most extreme first. Route it with **the same heuristic and van budget**. If it can't
  reach all 20 stations, do the most extreme first.
- **Comparison lines in the results:** our plan with `k = 0` (trust the forecast) and our plan
  with the chosen `k`.

---

## 5. How we test it

### 5.1 The idea in plain words
For each test morning we ask: if the stations had started at this policy's inventory, how many
people would have failed to get a bike or to return one between 06:00 and 10:00? We replay
the real morning, 5 minutes at a time, from each policy's starting point. Every policy faces
exactly the same riders; only the starting point differs.

### 5.2 The starting inventory of each policy at 06:00
Our vans work 00–06 and Bluebikes' don't work at night, so riders between 02:00 and 06:00 are the same for every policy:

```
start_i(policy) = clip( obs06_i + (reached_i − b_i) , 0 , C_i )
```

- Bluebikes (i): `reached_i = b_i`, so `start_i = obs06_i`.
- Greedy (ii) and ours: `reached_i` comes from the routing.

### 5.3 The replay
For each station, starting from `x = start_i`, apply the 48 cleaned 5-minute changes in order:
- `x ← x + Δ`
- if `x < 0`: **failed pickups += −x**, then `x = 0`
- if `x > C_i`: **failed returns += x − C_i**, then `x = C_i`

### 5.4 ⚠️ Hidden demand in the replay (D23, needs confirmation)
**The problem.** The flows we replay were produced under Bluebikes' real inventories. When a real
station was empty, would-be riders left and **left no trace** in the logs. So replaying Bluebikes'
own starting point reproduces the real morning exactly and shows about **zero failures by
construction**, while any other policy is penalized whenever it starts differently. That makes
benchmark (i) look unbeatable for the wrong reason.

**The fix.** For every 5-minute step where the **real** station had **0 bikes**, add the pickups
riders typically attempt at that station and hour:

```
extra pickups per step = (mean weekday pickups at station i in that hour, from trips) / 12
```

Use the censoring-corrected rate once Section 4.4 is done. Likewise, when the real station was
**full**, add typical returns. These extra attempts are added to every policy's replay:
- Under Bluebikes' starting point they become failures, because the station really was empty.
- Under a policy that kept bikes there, they're served.

This makes the comparison fair, and it's the PDF's censoring idea applied to the test. It needs
only trips + logs separately (typical rates from trips, empty/full moments from logs), so the missing overlap doesn't matter.

**Limitation that remains:** the logs still can't see offsetting events inside 5 minutes
(2 pickups + 2 returns look like 0). Absolute failure counts are approximate; comparisons between policies are what matter.

### 5.5 What we report per policy (total over the 11 test mornings, and per morning)
- **Failed pickups** and **failed returns** (raw counts, the headline)
- Weighted cost `e·FP + f·FR + λ·moved`
- Bikes moved, van minutes, stations visited
- For our plan: share of the LP targets the vans delivered
- Breakdown by type of station (residential vs job centres; top-20 most imbalanced)
- Data quality: van-move steps removed per morning, steps with hidden demand added

### 5.6 Bonus if November's trips arrive before Dec 7
- Re-check the 8-bike threshold: compare each removed jump with the trips in the same 5 minutes at that station.
- Re-score with trip-based flows for Nov 17–30.
- Redo the censoring correction with matched days.

---

## 6. Parameters and sensitivity analysis

| Parameter | Base case | Sensitivity | Source / reason |
|---|---|---|---|
| Vans | 4 | {2, 4, 6, 8} | Bluebikes media kit: 4–5 vans |
| Van capacity | 20 bikes | (none) | Media kit: 20–25 |
| Shift | 00:00–06:00 | (none) | D2 |
| Speed | 20 km/h on road distance | (none) | Boston traffic incl. stops |
| Road distance | straight × 1.3 → OSRM (upgrade) | straight vs OSRM | D20 |
| Time per stop | 5 min + 0.5 min/bike | (none) | Assumption |
| `e` (failed pickup) | $3 | `e/f` ∈ {2, 3, 5} | ≈ one ride |
| `f` (failed return) | $1 | (as above) | ≈ a detour |
| `λ` (per bike moved) | $1 | {0.5, 1, 2} | Bluebikes rebalancing paper: ~$1/bike |
| `k` (robustness) | chosen on Nov 12–14 from {1, 2} | chart incl. `k = 0` | D12 |
| Van-move threshold | 8 bikes / 5 min | (none) | Section 7.1 |
| Censoring cap | ×3 | (none) | D9 |
| Routing tries | 1,000 fleet plans | (none) | D15 |
| Random choice | among 3 closest useful stations | (none) | D15 |

Sweeps change **one parameter at a time** around the base case. The fleet-size sweep is the most
important: plot failures (y) against number of vans (x) for ours vs greedy. This answers
"cut cost for the same service, or serve more riders with the same fleet".

---

## 7. What the data already showed us

### 7.1 Threshold for van moves (analysis of Nov 10–23 logs, 2.2 M readings)
Riders-only baseline = November 2025 trips (they contain no vans).

| Jump ≥ (bikes / 5 min) | Per day in logs | Share riders alone could explain |
|---|---|---|
| 4 | 254 | ~49% |
| 5 | 102 | ~38% |
| 6 | 47 | ~30% |
| 7 | 23 | ~23% |
| **8** | **13** | **~18%** |
| 9 | 7 | ~15% |
| 10 | 3 | ~18% |

- At 4–6, a third to a half of "van moves" would actually be rider rushes. At 8 and above the
  riders' share stops falling, so 8 is where the curve flattens out.
- Big drop-offs have a nearby matching pickup (≤ 3 km, ≤ 45 min) 15–19 percentage points more
  often than chance: real evidence of vans, but it doesn't point to a threshold.
- Jumps of 7 or more never reverse within 10 minutes, so they're not feed glitches.
- They're concentrated at the MIT stations, Ames/Main (valet) and Longwood/Binney (valet).
- We could not link jumps to individual vans (no van data exists). Re-check with November's trips (5.6).

### 7.2 Bluebikes' vans don't work at night
Between 01:00 and 05:00 there were **0–2 jumps of 6 or more in two weeks**. Jumps follow the
working day (07:00–22:00), matching the media kit's "vans run 6am–10pm". Consequences:
- Benchmark (i) is the system after **no** overnight rebalancing. Our night shift is a real
  "what if the van hours moved to the night?" comparison. **A strong point for the report.**
- `out/rebalancing.parquet` (overnight van detection) will be almost empty. That's expected.
- It supports the daytime-shift upgrades (D3).

### 7.3 Valet stations (October 2026 weekday mornings)
| Station | Docks | Median net inflow 06–10 | Max |
|---|---|---|---|
| Ames St at Main St | 19 | +95 | +123 |
| Longwood Ave at Binney St | 15 | +73 | +93 |

Both are on Bluebikes' official valet list (Mon–Fri 7am–7pm). The next station is at 1.0×
capacity, so the gap is clear. ~37 stations sit at 0.7–1.0× capacity (Kendall, MIT, Seaport,
Financial District, Harvard Sq). **They fill up every morning and are the core of our problem; they stay in.**
Some (MIT Stata 46 vs 35 docks, MIT Carleton 37 vs 27, MIT Hayward 35 vs 27, Watermark Seaport
27 vs 15, Mass Ave/Albany 26 vs 18) sometimes take in more than capacity, which means Bluebikes
empties them during the morning. That's consistent with 7.1.

### 7.4 Public facts we cite
- Bluebikes media kit: 4–5 vans, 20–25 bikes each, 6am–10pm, 7 days a week. https://www.bluebikes.com/about/media-kit
- Harvard Crimson (2022): vans run ~20 h/day; depot team for heavy repairs. https://www.thecrimson.com/article/2022/12/1/bluebikes-boston-logistics/
- Bluebikes optimization report: ~$1 per bike moved. https://amberjiayusu.github.io/project/assets/Optimization_Final_Report.pdf
- Bluebikes valet stations: https://bluebikes.com/blog/commuter-valet-service , https://blog.bluebikes.com/about/valet

---

## 8. Assumptions and limitations

These go in the report, most of them in "how we tackled it" and "another week".
1. **Only the "prepare for the morning peak" job is modelled.** Bluebikes rebalances ~16–20 h/day (D4).
2. **No overlap between trips and logs:** forecast learned on trips, test on logs (D8).
3. **Censoring patterns learned in November are applied to earlier months** (D9).
4. **The test reads net flows from 5-minute log changes:** offsetting events inside 5 minutes are
   invisible; van moves are removed with an 8-bike threshold that is ~18% riders (D17).
5. **Hidden demand added in the replay uses typical hourly rates**, not the actual riders that morning (D23).
6. **Van parameters, depot location and costs are assumptions** based on public figures; sensitivity analysis covers the important ones.
7. **Road distances = straight × 1.3** until OSRM.
8. **The LP uses net flow over the window**, not the order of events; the replay captures the order.
9. **Weather "forecasts" are Open-Meteo's archived recent runs**, close to but not exactly the evening-before forecast.
10. **Only 3 tuning mornings and 11 test mornings**, all in late November; results may differ in summer.
11. **Valet stations excluded** (2 stations).
12. E-bikes and regular bikes are treated as the same.

---

## 9. Upgrades and the "if we had another week" list

**Upgrades we plan to do (in order, dropped if late):**
1. OLS forecast with weather + school flag (replaces the stand-in)
2. Choose `k` on tuning days
3. Censoring correction in the forecast
4. OSRM real road times
5. Sensitivity runs
6. Local search on routes
7. Second depot location

**"If we had another week" (report section vii):**
- **Rebalancing in two shifts:** night (00–06) for the morning + midday (~12–15) for the evening, covering the full weekday.
- **Vans running close to Bluebikes' real ~20 h/day:** re-run the pipeline every few hours, from "prepare for the peak" toward continuous rebalancing.
- Full-day objective (06–24) with the running cumulative net flow per station:
  `max(cumulative outflow) ≤ y_i ≤ C_i − max(cumulative inflow)`. Stations with no feasible
  range show where daytime rebalancing or more docks are needed.
- Weekends.
- Gradient boosting / pooled forecasting model across stations.
- Proper censoring correction and validation with November trips.
- Separate pickups and returns in the forecast instead of net flow.
- Other cities (NYC Citi Bike uses the same GBFS format).

---

## 10. Code layout

The existing pipeline stays as it is. New modelling code goes in `model/`. Run everything from the repo root.

```
model/
  config.py          all parameters from Section 6 in one place (vans, e, f, λ, k, threshold, dates)
  calendar.py        school flag, holidays, tuning/test day lists
  weather.py         download Open-Meteo forecasts -> data/weather/forecast_daily.csv
  valets.py          valet rule -> list of excluded stations
  log_flows.py       5-min changes 06-10 from logs, van-move removal, hidden-demand additions
  forecast.py        8-week baseline + OLS per station, October validation, s_i
  censoring.py       November empty/full patterns applied to trips (upgrade)
  lp.py              robust LP -> y_i
  routing.py         depot, simultaneous randomized nearest neighbour, 1,000 fleet plans
  benchmarks.py      greedy (ii) and Bluebikes (i)
  evaluate.py        replay and failure counts per policy per morning
  run_experiments.py runs everything for tuning/test days and the sweeps -> results/
results/             tables and charts for the report (committed)
```

---

## 11. CHECKLIST

Tick with `- [x]` and add `(done MM-DD, Name)`. Dates are targets.

### Done before this plan
- [x] GBFS logger running every 5 minutes on GitHub Actions (done 11-10)
- [x] Trip downloader + 12 months of trips, Nov 2025 → Oct 2026 (done 11-23)
- [x] Clean tables and stand-in instance builder, `build_all.py` (done 11-23)
- [x] Interim report submitted (done 11-06)
- [x] Planning session and this PLAN.md (done 11-27, Gianluca + Claude)
- [x] Van-move threshold analysis → 8 bikes / 5 min (done 11-27)
- [x] Valet rule applied → Ames/Main, Longwood/Binney (done 11-27)

### Decision still open
- [ ] **Confirm D23** (hidden demand added back in the replay, Section 5.4)

### Phase 1: first end-to-end version (target Sun Nov 29)
- [ ] Add `scipy` to `requirements.txt`; create `model/` and `model/config.py` with all base-case parameters
- [ ] `model/calendar.py`: holidays, tuning days (Nov 12–14), test days (Nov 17–20, 23–25, 30, Dec 1–3)
- [ ] `model/valets.py`: valet rule, saves the list; check it still gives the 2 stations
- [ ] `model/log_flows.py`: 06–10 cleaned 5-minute changes per station per morning; remove steps ≥ 8; count removals
- [ ] `model/log_flows.py`: hidden-demand additions when the real station was empty/full (D23)
- [ ] `model/evaluate.py`: replay (Section 5.3) → failed pickups/returns per station per morning
- [ ] Sanity check: replaying Bluebikes' `obs06` *without* hidden demand gives ~0 failures (proves the replay matches the logs)
- [ ] Benchmark (i): `obs06` as the starting point, scored on all test mornings
- [ ] `model/lp.py`: robust LP with the stand-in forecast (`f`, `s` from the 8-week mean/std), `k = 1`
- [ ] `model/routing.py`: depot (trip-weighted centre), simultaneous randomized nearest neighbour, 1,000 plans, return to depot empty
- [ ] Calibrate `M` (bikes the fleet can move in a shift) with the routing heuristic
- [ ] `model/benchmarks.py`: greedy (ii), routed with the same heuristic
- [ ] `model/run_experiments.py`: all policies × test mornings → `results/main_table.csv`
- [ ] **First full results table** (ours vs greedy vs Bluebikes)

### Phase 2: forecast upgrade (target Tue Dec 1)
- [ ] `model/weather.py`: download Open-Meteo historical forecasts Nov 2025 → Dec 2026, save `data/weather/forecast_daily.csv`
- [ ] School flag dates checked against MIT/BU calendars
- [ ] `model/forecast.py`: OLS per station (rain, temp, weekday dummies, school), small-station fallback
- [ ] Validate on October: MAE/RMSE vs 8-week baseline → `results/forecast_validation.csv`
- [ ] `s_i` from October errors; refit on all trips; forecast November mornings
- [ ] Save coefficients + standard errors; pick 2–3 example stations for the report
- [ ] Choose `k ∈ {1, 2}` on Nov 12–14; re-run main results with the OLS forecast and chosen `k` (plus `k = 0` line)

### Phase 3: other upgrades + sensitivity (target Thu Dec 3)
- [ ] `model/censoring.py`: run `merge.py`, apply November empty/full shares to trips (cap ×3), refit forecast, before/after numbers
- [ ] Use corrected rates for the hidden-demand additions in the replay
- [ ] OSRM road-time matrix downloaded once → `data/distances_osrm.parquet`; re-run routing; compare
- [ ] Sweep vans {2, 4, 6, 8}: chart failures vs vans, ours vs greedy
- [ ] Sweep `e/f` {2, 3, 5} and `λ` {0.5, 1, 2}
- [ ] `k` chart (0, 1, 2) on test days
- [ ] (if time) local search on routes; second depot location

### Phase 4: freeze and final run (Thu Dec 3)
- [ ] `git pull` after the Dec 3 morning is logged; rebuild `out/`
- [ ] Final run of all experiments → `results/` (tables + charts), committed
- [ ] Check whether November trips are published (bonus checks in 5.6)

### Phase 5: deliverables (Fri Dec 4 – Sun Dec 6)
- [ ] Report draft (5–10 pages) following Section 12
- [ ] Charts: map of stations by imbalance; failures by policy; vans sweep; `k` chart; example route map
- [ ] Slides
- [ ] Video (≤ 5 min, general audience), uploaded; link on page 1 of the report
- [ ] Proofread; both names on everything

### Submit (Mon Dec 7)
- [ ] Report + slides on Gradescope, one submission, both team members added
- [ ] Prepare for in-class presentation Dec 9–10

---

## 12. Report, slides and video outline

Following the professor's seven questions (5–10 pages, no appendix needed, but one can hold the valet list and parameter table):

1. **Which problem did we address?** Morning imbalance at Bluebikes; overnight van rebalancing; objective with `e > f`.
2. **Why does it matter?** Failed pickups = lost rides and revenue; failed returns = frustrated riders; van costs.
3. **Which data?** Trips (12 months), our own 5-minute inventory logs (the dataset nobody publishes), station info, Open-Meteo forecasts. The missing overlap and how we handled it. Valet stations. Bluebikes' vans don't work at night (7.2).
4. **How did we tackle it?** Forecast (OLS) → robust LP (interval, worst case at the endpoints) → simultaneous routing heuristic → replay evaluation with van moves removed and hidden demand added. Benchmarks.
5. **Key findings.** Failures ours vs greedy vs Bluebikes; effect of robustness (`k` chart); vans sweep; censoring correction before/after; stations that fail under any inventory.
6. **Impact.** Same service with fewer vans, or more completed rides with the same fleet; where to add docks; works in other GBFS cities.
7. **If we had another week.** Section 9.

**Video (≤ 5 min):** the problem in one picture (morning map of empty/full stations), the idea (choose morning inventories + routes), one headline number, one route animation or map, the impact.

---

## 13. Glossary

- **GBFS:** the live public feed of station status every bike-share system publishes. We save it every 5 minutes.
- **Net outflow:** pickups minus returns at a station over a period. Positive = the station loses bikes.
- **Censoring / hidden demand:** when a station is empty, people who wanted a bike leave and are never recorded, so observed trips understate true demand.
- **Dummy variable:** a 0/1 column representing a category (e.g. `tue = 1` on Tuesdays). One category is left out as the baseline.
- **OLS:** ordinary least squares, the standard linear regression.
- **Robust optimization:** plan for a range of possible outcomes (here, demand anywhere in an interval) instead of a single forecast.
- **LP (linear program):** an optimization problem where the objective and all constraints are linear; solved exactly and fast.
- **Heuristic:** a method that finds a good (not guaranteed best) solution quickly.
- **Tuning days vs test days:** settings are chosen on tuning days; results are reported only on test days the choice never saw.
- **Replay:** re-running a real morning's flows from a different starting inventory to count failures.
- **Valet station:** a station where staff stack overflow bikes in a corral, so it never really fills up.
- **Depot:** where vans start and end their shift.
- **OSRM:** a free routing engine that computes real driving times from OpenStreetMap.
- **Commit / push / pull:** save a change into the project's history / upload it to GitHub / download others' changes.
