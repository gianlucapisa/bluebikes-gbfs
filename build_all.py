#!/usr/bin/env python3
"""Rebuild everything in out/ from data/ (takes a few seconds, no network needed).

    git pull && python build_all.py
    python build_all.py --date 2026-11-20     # routing instance for a specific morning
"""
import subprocess
import sys

extra = sys.argv[1:]
for cmd in (["build_stations.py"], ["build_daily.py"], ["make_instance.py", *extra]):
    print(f"\n$ python {' '.join(cmd)}", flush=True)
    subprocess.run([sys.executable, *cmd], check=True)
