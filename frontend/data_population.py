"""
Population data by FIDE federation and year.

Primary source: World Bank API (SP.POP.TOTL indicator), downloaded once and cached.
Fallback: hardcoded approximations for FIDE-specific federations not in World Bank data
(ENG/SCO/WAL share ISO-3 GBR; FID has no country code).
"""
import os
import requests
import pandas as pd

CACHE_PATH = os.path.join(os.path.dirname(__file__), "pop_cache.parquet")

# FIDE codes that differ from ISO-3166-1 alpha-3
FIDE_TO_ISO3: dict[str, str] = {
    "GER": "DEU", "ENG": "GBR", "SCO": "GBR", "WAL": "GBR",
    "NED": "NLD", "ICE": "ISL", "DEN": "DNK", "SUI": "CHE",
    "CHI": "CHL", "PHI": "PHL", "MAS": "MYS", "INA": "IDN",
    "NGR": "NGA", "GRE": "GRC", "ROM": "ROU", "RSA": "ZAF",
    "ALG": "DZA", "VIE": "VNM", "IRI": "IRN", "BUL": "BGR",
    "CRO": "HRV", "FAI": "FRO", "MNE": "MNE", "BIH": "BIH",
    "MAR": "MAR", "LBA": "LBY", "SUD": "SDN", "KOS": "XKX",
    "TPE": "TWN", "MGL": "MNG", "SRI": "LKA", "BAN": "BGD",
    "MYA": "MMR", "CAM": "KHM", "URU": "URY", "PAR": "PRY",
    "ESA": "SLV", "GUA": "GTM", "HON": "HND", "NCA": "NIC",
    "DOM": "DOM", "HAI": "HTI", "TTO": "TTO", "GUY": "GUY",
    "SUR": "SUR", "BLZ": "BLZ", "PUR": "PRI", "RSA": "ZAF",
    "CMR": "CMR", "SEN": "SEN", "MLI": "MLI", "BUR": "BFA",
    "TOG": "TGO", "BEN": "BEN", "GUI": "GIN", "SLE": "SLE",
    "LBR": "LBR", "GBS": "GNB", "CPV": "CPV", "GAB": "GAB",
    "CGO": "COG", "CAF": "CAF", "ANG": "AGO", "ZAM": "ZMB",
    "ZIM": "ZWE", "BOT": "BWA", "NAM": "NAM", "SWZ": "SWZ",
    "LSO": "LSO", "TAN": "TZA", "UGA": "UGA", "RWA": "RWA",
    "ERI": "ERI", "DJI": "DJI", "SOM": "SOM", "COM": "COM",
    "SEY": "SYC", "MRI": "MUS", "PNG": "PNG", "FIJ": "FJI",
    "SOL": "SLB", "VAN": "VUT", "SAM": "WSM", "TGA": "TON",
    "HKG": "HKG", "MAC": "MAC", "PRK": "PRK",
}

# UK sub-nation shares (based on 2021 census proportions)
_GBR_SHARES = {"ENG": 0.840, "SCO": 0.081, "WAL": 0.047}

# Hardcoded fallbacks for federations without ISO-3 mapping
_FALLBACK_M: dict[str, float] = {
    "FID": 0.0,   # "Fide" (stateless/multiple)
    "KOS": 1.78,  # Kosovo (not in World Bank standard)
    "TPE": 23.6,  # Taiwan (not in WB as separate)
    "PUR": 3.19,  # Puerto Rico (US territory, not in WB separate)
}


def _download_wb() -> pd.DataFrame:
    """Fetch total population from World Bank API for 2009–2026."""
    resp = requests.get(
        "https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL",
        params={"format": "json", "per_page": 20000, "date": "2009:2026"},
        timeout=30,
    )
    resp.raise_for_status()
    records = resp.json()[1]

    rows = []
    for r in records:
        iso3 = r.get("countryiso3code", "")
        val = r.get("value")
        if not iso3 or len(iso3) != 3 or val is None:
            continue
        rows.append({"iso3": iso3, "year": int(r["date"]), "pop_m": val / 1_000_000})

    return pd.DataFrame(rows)


def _build_fide_pop(wb: pd.DataFrame) -> pd.DataFrame:
    """Map World Bank ISO-3 data to FIDE federation codes."""
    # Build reverse mapping: iso3 → primary FIDE code
    # (for 1:1 countries, FIDE code IS the ISO-3 code)
    iso3_to_fide_primary: dict[str, str] = {}
    for fide, iso3 in FIDE_TO_ISO3.items():
        if fide not in _GBR_SHARES:  # handle GBR separately
            if iso3 not in iso3_to_fide_primary:
                iso3_to_fide_primary[iso3] = fide

    rows = []
    for _, r in wb.iterrows():
        iso3 = r["iso3"]
        year = r["year"]
        pop_m = r["pop_m"]

        # UK sub-nations
        if iso3 == "GBR":
            for fed, share in _GBR_SHARES.items():
                rows.append({"federation": fed, "year": year, "pop_m": pop_m * share})
            continue

        # Countries in the exception mapping
        if iso3 in iso3_to_fide_primary:
            rows.append({"federation": iso3_to_fide_primary[iso3], "year": year, "pop_m": pop_m})
        else:
            # FIDE code == ISO-3 for the majority of countries
            rows.append({"federation": iso3, "year": year, "pop_m": pop_m})

    # Add fallback entries for all years
    years = wb["year"].unique()
    for fed, pop in _FALLBACK_M.items():
        for y in years:
            rows.append({"federation": fed, "year": int(y), "pop_m": pop})

    return pd.DataFrame(rows).drop_duplicates(["federation", "year"])


def load_population(rebuild: bool = False) -> pd.DataFrame:
    """Return DataFrame: federation, year, pop_m — from WB cache or download."""
    if not rebuild and os.path.exists(CACHE_PATH):
        return pd.read_parquet(CACHE_PATH)

    print("Downloading population data from World Bank API…")
    wb = _download_wb()
    df = _build_fide_pop(wb)
    df.to_parquet(CACHE_PATH, index=False)
    print(f"Cached {len(df)} rows → {CACHE_PATH}")
    return df


def get_pop_lookup(df: pd.DataFrame) -> dict[tuple[str, int], float]:
    """Return {(federation, year): pop_m} for fast lookup."""
    return {(r["federation"], r["year"]): r["pop_m"]
            for _, r in df.iterrows()}


# Static fallback for code that still uses the old dict (e.g. initial startup)
POPULATION_M: dict[str, float] = {}  # populated lazily from WB data
