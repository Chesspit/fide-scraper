"""Extract Top-100 players by standard rating from FIDE ZIP snapshots (one per year)."""
import os, re, zipfile
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "top100_cache.parquet")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _find_yearly_zips() -> dict[int, tuple[str, int]]:
    """Ein Snapshot pro Jahr: frühester verfügbarer Monat (i.d.R. Januar) für
    Jahresvergleichbarkeit — außer für das jüngste (laufende, noch unvollständige)
    Jahr in den Daten, das den neuesten verfügbaren Monat nutzt, damit Grafik/Tabelle
    den aktuellen Stand zeigen statt einen veralteten Januar-Schnappschuss.

    Rückgabe: {jahr: (pfad, monat)} — der Monat wird mit zurückgegeben, damit das
    tatsächliche Snapshot-Datum (nicht nur das Jahr) für die X-Achse verfügbar ist.
    """
    candidates: dict[int, dict[int, str]] = {}
    for fname in os.listdir(DATA_DIR):
        if not fname.endswith(".zip"):
            continue
        m = re.search(r"([a-z]{3})(\d{2})", fname)
        if not m:
            continue
        mon, yr2 = m.group(1), m.group(2)
        if mon not in MONTH_MAP:
            continue
        year = 2000 + int(yr2)
        if year < 2009 or year > 2026:
            continue
        month = MONTH_MAP[mon]
        candidates.setdefault(year, {})[month] = os.path.join(DATA_DIR, fname)

    if not candidates:
        return {}
    latest_year = max(candidates)
    best: dict[int, tuple[str, int]] = {}
    for year, months in candidates.items():
        pick_month = max(months) if year == latest_year else min(months)
        best[year] = (months[pick_month], pick_month)
    return dict(sorted(best.items()))


def _parse_top100(path: str, year: int, month: int) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            raw = f.read().decode("latin-1", errors="replace").splitlines()

    if not raw:
        return pd.DataFrame()

    header = raw[0]

    # Detect rating column by month pattern (e.g. 'Jan09' or 'jan20')
    m = re.search(r"[A-Za-z]{3}\d{2}", header)
    if not m:
        return pd.DataFrame()
    rat_pos = m.start()

    fed_pos = header.index("Fed") if "Fed" in header else 48
    id_end = 15 if "ID Number" in header else 10
    # Pre-2013: "Titl" (4 chars) sits between name and Fed
    name_end = fed_pos - 4 if "Titl" in header else fed_pos

    records = []
    for line in raw[1:]:
        if len(line) < rat_pos + 4:
            continue
        # Rating
        try:
            rating = int(line[rat_pos:rat_pos + 5].strip())
        except ValueError:
            continue
        if rating < 2000:
            continue
        # Federation
        fed = line[fed_pos:fed_pos + 3].strip().upper()
        if not fed or len(fed) != 3:
            continue
        # Name
        name = line[id_end:name_end].strip()
        # FIDE ID
        try:
            fide_id = int(line[:id_end].strip())
        except ValueError:
            continue

        records.append({
            "fide_id": fide_id,
            "name": name,
            "federation": fed,
            "rating": rating,
            "year": year,
            "period": pd.Timestamp(year=year, month=month, day=1),
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df[df["rating"] > 0].nlargest(200, "rating").reset_index(drop=True)
    df["rank"] = df.index + 1
    return df


def load_top100(rebuild: bool = False) -> pd.DataFrame:
    """Return DataFrame: year, period, rank, fide_id, name, federation, rating."""
    if not rebuild and os.path.exists(CACHE_PATH):
        return pd.read_parquet(CACHE_PATH)

    frames = []
    for year, (path, month) in _find_yearly_zips().items():
        df = _parse_top100(path, year, month)
        if not df.empty:
            frames.append(df)

    result = pd.concat(frames, ignore_index=True)
    result.to_parquet(CACHE_PATH, index=False)
    return result
