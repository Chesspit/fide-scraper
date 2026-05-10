"""Load GM/IM title counts per country and year from FIDE TXT snapshots."""
import os, re, zipfile
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "title_cache.parquet")

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# FIDE federation → continent
CONTINENT = {
    # Europe
    **dict.fromkeys(
        ["GER","FRA","RUS","ENG","POL","NED","HUN","CZE","AUT","SUI","SWE","NOR",
         "DEN","FIN","ESP","ITA","ROM","BUL","GRE","SRB","CRO","SLO","SVK","UKR",
         "BLR","LAT","LTU","EST","MDA","ARM","GEO","AZE","FID","POR","BEL","ICE",
         "LUX","AND","MNE","BIH","ALB","MKD","MLT","CYP","TUR","KOS","ISR","SCO",
         "WAL","IRL","FAI","ISL","MNC","SMR","LIE"], "Europe"),
    # Asia
    **dict.fromkeys(
        ["CHN","IND","JPN","KOR","VIE","PHI","IRI","IRQ","UZB","KAZ","MGL","TKM",
         "KGZ","TJK","SRI","BAN","PAK","NEP","MAS","SGP","INA","THA","MYA","HKG",
         "MAC","TPE","UAE","JOR","LIB","SYR","YEM","KUW","QAT","SAU","BRN","AFG",
         "MDV","BTN","LAO","CAM","TLS","PRK","MYA","BRU"], "Asia"),
    # Americas
    **dict.fromkeys(
        ["USA","CUB","ARG","BRA","MEX","COL","PER","VEN","CHI","URU","PAR","BOL",
         "ECU","GUA","CAN","JAM","TTO","DOM","PAN","CRC","HON","NCA","PUR","ESA",
         "HAI","GUY","SUR","BAR","BAH","ANT","GRN","SKN","LCA","VIN","DMA",
         "BLZ","ATG"], "Americas"),
    # Africa
    **dict.fromkeys(
        ["RSA","EGY","MAR","TUN","ALG","NGR","KEN","ZIM","BOT","MDG","MOZ","TAN",
         "ZAM","GHA","MLI","SEN","CIV","CMR","LBA","SUD","ETH","UGA","RWA","ANG",
         "BUR","DJI","MRI","SEY","BEN","NAM","TOG","NIG","GAB","CGO","COM","CPV",
         "GBS","GUI","LBR","SLE","SOM","SWZ","CAF","TCD","ERI","LSO"], "Africa"),
    # Oceania
    **dict.fromkeys(
        ["AUS","NZL","PNG","FIJ","SOL","VAN","SAM","TGA","COK","PLW"], "Oceania"),
}


def _find_yearly_zips():
    """One ZIP per year (prefer January) from 2009–2026."""
    best = {}  # year -> (path, month)
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
        path = os.path.join(DATA_DIR, fname)
        if year not in best or month < best[year][1]:
            best[year] = (path, month)
    return {y: p for y, (p, _) in sorted(best.items())}


def _parse_zip(path):
    """Count GMs and IMs per federation from one ZIP snapshot."""
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            raw = f.read().decode("latin-1", errors="replace").splitlines()

    if not raw:
        return pd.DataFrame()

    header = raw[0]

    # Detect format from header
    if "Titl" in header:
        # Pre-2013: single-char codes ('g','m'), Titl then Fed
        tit_pos = header.index("Titl")
        fed_pos = header.index("Fed")
        tit_len = 4
        gm_vals = {"g"}
        im_vals = {"m"}
    else:
        # Post-2013: two-char codes ('GM','IM'), Fed then Tit
        fed_pos = header.index("Fed")
        tit_pos = fed_pos + 8   # 'Fed Sex Tit' → Tit starts 8 chars after Fed
        tit_len = 5
        gm_vals = {"GM"}
        im_vals = {"IM"}

    gm_counts: dict = {}
    im_counts: dict = {}

    for line in raw[1:]:
        if len(line) < fed_pos + 3:
            continue
        fed = line[fed_pos:fed_pos + 3].strip().upper()
        if not fed or len(fed) != 3:
            continue
        tit = line[tit_pos:tit_pos + tit_len].strip()
        if tit in gm_vals:
            gm_counts[fed] = gm_counts.get(fed, 0) + 1
        elif tit in im_vals:
            im_counts[fed] = im_counts.get(fed, 0) + 1

    feds = set(gm_counts) | set(im_counts)
    return pd.DataFrame([
        {"federation": f,
         "gm": gm_counts.get(f, 0),
         "im": im_counts.get(f, 0)}
        for f in feds
    ])


def load_title_evolution(rebuild=False) -> pd.DataFrame:
    """Return DataFrame: year, federation, continent, gm, im."""
    if not rebuild and os.path.exists(CACHE_PATH):
        return pd.read_parquet(CACHE_PATH)

    rows = []
    for year, path in _find_yearly_zips().items():
        df = _parse_zip(path)
        if df.empty:
            continue
        df["year"] = year
        rows.append(df)

    result = pd.concat(rows, ignore_index=True)
    result["continent"] = result["federation"].map(CONTINENT).fillna("Other")

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    result.to_parquet(CACHE_PATH, index=False)
    return result
