#!/usr/bin/env python3
"""
Bluebikes GBFS logger.

Takes `--count` snapshots of station_status, `--interval` seconds apart, and writes
each snapshot to   data/status/YYYY-MM-DD/HHMMSS.csv.gz   (UTC times).
Once per UTC day it also saves station_information to
data/station_info/YYYY-MM-DD.csv.gz (id -> name, short_name, lat, lon, capacity).

Standard library only: no dependencies that can break.
"""
import argparse
import csv
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("GBFS_BASE", "https://gbfs.lyft.com/gbfs/1.1/bos/en/")
STATUS_URL = BASE + "station_status.json"
INFO_URL = BASE + "station_information.json"
OUT = Path(os.environ.get("GBFS_OUT", "data"))

STATUS_FIELDS = [
    "snapshot_utc",          # when WE fetched (UTC, ISO)
    "feed_last_updated",     # feed timestamp (unix seconds)
    "station_id",
    "legacy_id",
    "num_bikes_available",
    "num_ebikes_available",
    "num_bikes_disabled",
    "num_docks_available",
    "num_docks_disabled",
    "is_installed",
    "is_renting",
    "is_returning",
    "last_reported",         # last time the station itself reported (unix seconds)
]


def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}] {msg}", file=sys.stderr, flush=True)


def fetch(url, tries=4):
    """GET + parse JSON with retries and exponential backoff."""
    err = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mit-15c57-bluebikes-research"})
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.load(r)
            stations = payload["data"]["stations"]
            if not stations:
                raise ValueError("empty station list")
            return payload
        except Exception as e:  # network error, bad JSON, missing keys...
            err = e
            wait = 3 * 2 ** k
            log(f"fetch attempt {k + 1}/{tries} failed ({e!r}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}: {err!r}")


def write_gz_csv(path, rows, fields):
    """Atomic write: a half-written file never stays on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def to_cell(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, separators=(",", ":"))
    return v


def snapshot_status():
    now = datetime.now(timezone.utc)
    payload = fetch(STATUS_URL)
    rows = []
    for s in payload["data"]["stations"]:
        r = {k: to_cell(s.get(k)) for k in STATUS_FIELDS}
        r["snapshot_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        r["feed_last_updated"] = payload.get("last_updated")
        rows.append(r)
    path = OUT / "status" / f"{now:%Y-%m-%d}" / f"{now:%H%M%S}.csv.gz"
    write_gz_csv(path, rows, STATUS_FIELDS)
    log(f"saved {len(rows)} stations -> {path}")


def snapshot_info_daily():
    now = datetime.now(timezone.utc)
    path = OUT / "station_info" / f"{now:%Y-%m-%d}.csv.gz"
    if path.exists():
        return
    payload = fetch(INFO_URL)
    stations = payload["data"]["stations"]
    fields = sorted({k for s in stations for k in s})
    rows = [{k: to_cell(s.get(k)) for k in fields} for s in stations]
    write_gz_csv(path, rows, fields)
    log(f"saved station_information ({len(rows)} stations) -> {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="number of snapshots")
    ap.add_argument("--interval", type=int, default=300, help="seconds between snapshots")
    a = ap.parse_args()

    try:
        snapshot_info_daily()
    except Exception as e:
        log(f"station_information failed: {e!r}")  # non-fatal

    ok = 0
    for i in range(a.count):
        t0 = time.time()
        try:
            snapshot_status()
            ok += 1
        except Exception as e:
            log(f"snapshot {i + 1} failed: {e!r}")  # keep going
        if i < a.count - 1:
            time.sleep(max(0, a.interval - (time.time() - t0)))

    log(f"done: {ok}/{a.count} snapshots ok")
    sys.exit(0 if ok > 0 else 1)  # red X in GitHub Actions only if everything failed


if __name__ == "__main__":
    main()
