"""Build and load ELO percentile bands from FIDE ZIP snapshots."""
import os, re, sys, zipfile
from datetime import date

import numpy as np
import pandas as pd

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from data_titles import CONTINENT  # noqa: E402

DATA_DIR   = os.path.join(os.path.dirname(_here), "data")
CACHE_PATH = os.path.join(_here, "percentile_cache.parquet")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

PERCENTILES = [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 98, 99]

_EUROPE = {f for f, c in CONTINENT.items() if c == "Europe"}

SCOPES: dict[str, dict] = {
    "GER":    {"label": "Deutschland (GER)", "federations": {"GER"}},
    "AUT":    {"label": "Österreich (AUT)",  "federations": {"AUT"}},
    "SUI":    {"label": "Schweiz (SUI)",     "federations": {"SUI"}},
    "Europe": {"label": "Europa",            "federations": _EUROPE},
    "World":  {"label": "Weltweit",          "federations": None},
}


def _find_all_zips() -> list[tuple[date, str]]:
    result = []
    for fname in os.listdir(DATA_DIR):
        if not fname.endswith(".zip"):
            continue
        m = re.search(r"([a-z]{3})(\d{2})frl", fname)
        if not m:
            continue
        mon, yr2 = m.group(1), m.group(2)
        if mon not in MONTH_MAP:
            continue
        year = 2000 + int(yr2)
        if year < 2009:
            continue
        period = date(year, MONTH_MAP[mon], 1)
        result.append((period, os.path.join(DATA_DIR, fname)))
    return sorted(result)


def _parse_fed_ratings(path: str) -> dict[str, list[int]]:
    """Return {federation: [ratings]} for all players with rating > 0."""
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            raw = f.read().decode("latin-1", errors="replace").splitlines()

    if not raw:
        return {}

    header = raw[0]
    m = re.search(r"[A-Za-z]{3}\d{2}", header)
    if not m:
        return {}
    rat_pos = m.start()
    fed_pos = header.index("Fed") if "Fed" in header else 48

    result: dict[str, list[int]] = {}
    for line in raw[1:]:
        if len(line) < rat_pos + 4:
            continue
        try:
            rating = int(line[rat_pos:rat_pos + 5].strip())
        except ValueError:
            continue
        if rating <= 0:
            continue
        fed = line[fed_pos:fed_pos + 3].strip().upper()
        if not fed or len(fed) != 3:
            continue
        result.setdefault(fed, []).append(rating)

    return result


def build_cache(force_rebuild: bool = False) -> None:
    if not force_rebuild and os.path.exists(CACHE_PATH):
        print(f"Cache already exists: {CACHE_PATH}  (use --rebuild to force)")
        return

    all_zips = _find_all_zips()
    print(f"Building percentile cache: {len(all_zips)} ZIPs × {len(SCOPES)} scopes")

    rows = []
    for i, (period, zip_path) in enumerate(all_zips, 1):
        print(f"  [{i:3d}/{len(all_zips)}] {period} ...", end=" ", flush=True)
        try:
            fed_ratings = _parse_fed_ratings(zip_path)
        except Exception as e:
            print(f"ERROR: {e}")
            continue

        for scope_key, scope_def in SCOPES.items():
            feds = scope_def["federations"]
            if feds is None:
                all_r = [r for rs in fed_ratings.values() for r in rs]
            else:
                all_r = [r for fed, rs in fed_ratings.items() if fed in feds for r in rs]

            if len(all_r) < 20:
                continue

            pcts = np.percentile(all_r, PERCENTILES)
            sorted_r = sorted(all_r, reverse=True)
            n = len(sorted_r)
            rows.append({
                "period":    pd.Timestamp(period),
                "scope":     scope_key,
                "p5":        float(pcts[0]),
                "p10":       float(pcts[1]),
                "p20":       float(pcts[2]),
                "p30":       float(pcts[3]),
                "p40":       float(pcts[4]),
                "p50":       float(pcts[5]),
                "p60":       float(pcts[6]),
                "p70":       float(pcts[7]),
                "p80":       float(pcts[8]),
                "p90":       float(pcts[9]),
                "p95":       float(pcts[10]),
                "p98":       float(pcts[11]),
                "p99":       float(pcts[12]),
                "top100":    float(sorted_r[99])  if n >= 100  else None,
                "top50":     float(sorted_r[49])  if n >= 50   else None,
                "top10":     float(sorted_r[9])   if n >= 10   else None,
                "top1":      float(sorted_r[0])   if n >= 1    else None,
                "n_players": n,
            })

        print(f"ok ({len(fed_ratings)} feds, {sum(len(v) for v in fed_ratings.values()):,} players)")

    df = pd.DataFrame(rows)
    df.to_parquet(CACHE_PATH, index=False, engine="pyarrow", compression="zstd")
    print(f"\nSaved {len(df)} rows → {CACHE_PATH}")


def load_percentiles() -> pd.DataFrame:
    if not os.path.exists(CACHE_PATH):
        build_cache()
    return pd.read_parquet(CACHE_PATH)


if __name__ == "__main__":
    build_cache(force_rebuild="--rebuild" in sys.argv)
