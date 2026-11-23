#!/usr/bin/env python3
"""
Download Bluebikes monthly trip histories and save each month as a clean parquet file.

Bluebikes publishes one file per month (about a month late) in the public S3 bucket
behind https://bluebikes.com/system-data. File names are inconsistent
(".zip", ".csv.zip", ".csv"), so we list the bucket instead of guessing URLs.

    python fetch_trips.py                    # every month since --since that we don't have yet
    python fetch_trips.py --since 202501     # go further back
    python fetch_trips.py --months 202610    # just these months (re-downloads)

Output: data/trips/YYYYMM.parquet, one row per trip:
    rideable_type, member_casual,
    started_at, ended_at            (tz-aware, America/New_York, to the second)
    duration_min,
    start_short, end_short          (station code, e.g. "M32022" = short_name in GBFS)
    start_name, end_name, start_lat, start_lng, end_lat, end_lng
Only the post-April-2023 file layout is supported (older files use different columns).
"""
import argparse
import io
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

BUCKET = "https://s3.amazonaws.com/hubway-data/"
OUT = Path("data/trips")
TZ = "America/New_York"
FIRST_SUPPORTED = "202304"
NAME_RE = re.compile(r"^(\d{6})-bluebikes-tripdata\.(zip|csv\.zip|csv)$")
UA = {"User-Agent": "mit-15c57-bluebikes-research"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def list_months():
    """Return {YYYYMM: object key} for all monthly trip files in the bucket."""
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    found, token = {}, None
    while True:
        url = BUCKET + "?list-type=2" + (f"&continuation-token={urllib.parse.quote(token)}" if token else "")
        root = ET.fromstring(get(url))
        for c in root.findall("s3:Contents", ns):
            m = NAME_RE.match(c.find("s3:Key", ns).text)
            if m:
                found[m.group(1)] = m.group(0)
        if root.findtext("s3:IsTruncated", namespaces=ns) != "true":
            return found
        token = root.findtext("s3:NextContinuationToken", namespaces=ns)


def read_raw(key, blob):
    if key.endswith(".csv"):
        return pd.read_csv(io.BytesIO(blob), low_memory=False)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        csvs = [n for n in z.namelist() if n.endswith(".csv") and not n.startswith("__MACOSX")]
        if len(csvs) != 1:
            raise ValueError(f"{key}: expected 1 csv inside, found {csvs}")
        with z.open(csvs[0]) as f:
            return pd.read_csv(f, low_memory=False)


def clean(raw):
    need = {"rideable_type", "started_at", "ended_at", "start_station_id",
            "end_station_id", "start_station_name", "end_station_name",
            "start_lat", "start_lng", "end_lat", "end_lng", "member_casual"}
    missing = need - set(raw.columns)
    if missing:
        raise ValueError(f"unexpected file layout, missing columns {sorted(missing)}")
    df = pd.DataFrame({
        "rideable_type": raw["rideable_type"].astype("category"),
        "member_casual": raw["member_casual"].astype("category"),
        # timestamps in the files are Boston local time without an offset
        "started_at": pd.to_datetime(raw["started_at"], format="mixed")
                        .dt.floor("s")
                        .dt.tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward"),
        "ended_at": pd.to_datetime(raw["ended_at"], format="mixed").dt.floor("s")
                        .dt.tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward"),
        "start_short": raw["start_station_id"].astype("category"),
        "end_short": raw["end_station_id"].astype("category"),
        "start_name": raw["start_station_name"].astype("category"),
        "end_name": raw["end_station_name"].astype("category"),
        # float32 keeps ~1 m precision and halves the file size
        "start_lat": raw["start_lat"].astype("float32"), "start_lng": raw["start_lng"].astype("float32"),
        "end_lat": raw["end_lat"].astype("float32"), "end_lng": raw["end_lng"].astype("float32"),
    })
    df["duration_min"] = ((df["ended_at"] - df["started_at"]).dt.total_seconds() / 60).astype("float32")
    return df.sort_values("started_at", ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    default_since = f"{date.today().year - 1}{date.today().month:02d}"
    ap.add_argument("--since", default=default_since, help="first month YYYYMM (default: 12 months ago)")
    ap.add_argument("--months", nargs="*", help="exact months YYYYMM to (re)download")
    a = ap.parse_args()

    available = list_months()
    if a.months:
        todo = [m for m in a.months if m in available]
        for m in set(a.months) - set(available):
            print(f"{m}: not published yet", file=sys.stderr)
    else:
        since = max(a.since, FIRST_SUPPORTED)
        todo = [m for m in sorted(available) if m >= since and not (OUT / f"{m}.parquet").exists()]
    if not todo:
        print(f"nothing to do (latest published month: {max(available)})")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for m in todo:
        key = available[m]
        print(f"{m}: downloading {key} ...", flush=True)
        df = clean(read_raw(key, get(BUCKET + key)))
        path = OUT / f"{m}.parquet"
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp, index=False, compression="zstd")
        tmp.replace(path)
        no_station = (df["start_short"].isna() | df["end_short"].isna()).mean()
        print(f"{m}: {len(df):,} trips -> {path} "
              f"({path.stat().st_size / 1e6:.1f} MB, {no_station:.1%} missing a station)")


if __name__ == "__main__":
    main()
