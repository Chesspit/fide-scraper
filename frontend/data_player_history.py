"""Build and load per-player ELO history from all FIDE ZIP snapshots."""
import os, re, sys, zipfile
from datetime import date

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_here = os.path.dirname(os.path.abspath(__file__))
DATA_DIR        = os.path.join(os.path.dirname(_here), "data")
HISTORY_PATH    = os.path.join(_here, "player_history_cache.parquet")
META_PATH       = os.path.join(_here, "player_meta_cache.parquet")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Minimum rating to store (avoids provisional/ghost entries)
MIN_RATING = 100


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
        result.append((date(year, MONTH_MAP[mon], 1), os.path.join(DATA_DIR, fname)))
    return sorted(result)


def _parse_zip(path: str) -> list[tuple[int, str, str, int]]:
    """Return list of (fide_id, name, federation, rating) for all rated players."""
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            raw = f.read().decode("latin-1", errors="replace").splitlines()

    if not raw:
        return []

    header = raw[0]

    m = re.search(r"[A-Za-z]{3}\d{2}", header)
    if not m:
        return []
    rat_pos = m.start()

    fed_pos  = header.index("Fed") if "Fed" in header else 48
    id_end   = 15 if "ID Number" in header else 10
    name_end = fed_pos - 4 if "Titl" in header else fed_pos

    records = []
    for line in raw[1:]:
        if len(line) < rat_pos + 4:
            continue
        try:
            rating = int(line[rat_pos:rat_pos + 5].strip())
        except ValueError:
            continue
        if rating < MIN_RATING:
            continue
        fed = line[fed_pos:fed_pos + 3].strip().upper()
        if not fed or len(fed) != 3:
            continue
        try:
            fide_id = int(line[:id_end].strip())
        except ValueError:
            continue
        if fide_id <= 0:
            continue
        name = line[id_end:name_end].strip()
        records.append((fide_id, name, fed, rating))

    return records


def build_cache(force_rebuild: bool = False) -> None:
    if not force_rebuild and os.path.exists(HISTORY_PATH) and os.path.exists(META_PATH):
        print(f"Cache already exists. Use --rebuild to force.")
        return

    all_zips = _find_all_zips()
    print(f"Building player history cache: {len(all_zips)} ZIPs")

    # Write history parquet incrementally (row-group per ZIP)
    schema_hist = pa.schema([
        ("fide_id", pa.int32()),
        ("period",  pa.timestamp("ms")),
        ("rating",  pa.int16()),
    ])

    # Track meta info across all ZIPs
    # meta_map: fide_id -> {name, federation, peak_rating, last_rating, n_periods, last_period}
    meta_map: dict[int, list] = {}  # fide_id -> [name, fed, peak, last, n, last_ts]

    with pq.ParquetWriter(HISTORY_PATH, schema_hist, compression="zstd") as writer:
        for i, (period, zip_path) in enumerate(all_zips, 1):
            print(f"  [{i:3d}/{len(all_zips)}] {period} ...", end=" ", flush=True)
            try:
                records = _parse_zip(zip_path)
            except Exception as e:
                print(f"ERROR: {e}")
                continue

            if not records:
                print("empty")
                continue

            ts = pd.Timestamp(period).value // 10**6  # ms since epoch

            fids   = np.array([r[0] for r in records], dtype=np.int32)
            ratings = np.array([r[3] for r in records], dtype=np.int16)
            periods = np.full(len(records), ts, dtype=np.int64)

            batch = pa.record_batch({
                "fide_id": pa.array(fids,    type=pa.int32()),
                "period":  pa.array(periods, type=pa.timestamp("ms")),
                "rating":  pa.array(ratings, type=pa.int16()),
            })
            writer.write_batch(batch)

            # Update meta
            for fid, name, fed, rat in records:
                if fid not in meta_map:
                    meta_map[fid] = [name, fed, rat, rat, 1, period]
                else:
                    entry = meta_map[fid]
                    if rat > entry[2]:
                        entry[2] = rat           # peak
                    if period >= entry[5]:
                        entry[0] = name          # latest name
                        entry[1] = fed           # latest fed
                        entry[3] = rat           # last rating
                        entry[5] = period
                    entry[4] += 1                # n_periods

            print(f"ok ({len(records):,} players)")

    print(f"History written: {HISTORY_PATH}")

    # Build meta parquet
    print("Building meta cache...", end=" ", flush=True)
    fids    = list(meta_map.keys())
    names   = [meta_map[f][0] for f in fids]
    feds    = [meta_map[f][1] for f in fids]
    peaks   = np.array([meta_map[f][2] for f in fids], dtype=np.int16)
    lasts   = np.array([meta_map[f][3] for f in fids], dtype=np.int16)
    npers   = np.array([meta_map[f][4] for f in fids], dtype=np.int16)

    meta_df = pd.DataFrame({
        "fide_id":     pd.array(fids, dtype="int32"),
        "name":        names,
        "federation":  feds,
        "peak_rating": peaks,
        "last_rating": lasts,
        "n_periods":   npers,
    })
    meta_df.to_parquet(META_PATH, index=False, engine="pyarrow", compression="zstd")
    print(f"ok — {len(meta_df):,} unique players → {META_PATH}")


# ---------------------------------------------------------------------------
# Runtime load functions (used by elo_dist.py)
# ---------------------------------------------------------------------------

_meta_cache: pd.DataFrame | None = None
_player_cache: dict[int, pd.DataFrame] = {}  # LRU-light: fide_id → history df


def load_player_meta() -> pd.DataFrame:
    global _meta_cache
    if _meta_cache is None:
        if not os.path.exists(META_PATH):
            build_cache()
        _meta_cache = pd.read_parquet(META_PATH)
    return _meta_cache


def load_player_history(fide_id: int) -> pd.DataFrame:
    """Return DataFrame(period, rating) for one player. Uses pyarrow filter — no full load."""
    if fide_id in _player_cache:
        return _player_cache[fide_id]

    if not os.path.exists(HISTORY_PATH):
        build_cache()

    table = pq.read_table(
        HISTORY_PATH,
        columns=["period", "rating"],
        filters=[("fide_id", "=", fide_id)],
    )
    df = table.to_pandas()
    df["period"] = pd.to_datetime(df["period"])
    df = df.sort_values("period").reset_index(drop=True)

    if len(_player_cache) < 200:   # cap memory use
        _player_cache[fide_id] = df

    return df


if __name__ == "__main__":
    build_cache(force_rebuild="--rebuild" in sys.argv)
