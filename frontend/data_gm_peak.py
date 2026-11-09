"""Story E1 „Wann ist ein Großmeister am stärksten?" — Daten.

Quelle ohne DB: Monatsratings aus player_history_cache.parquet (FIDE-Listen ab
2009) plus Titel und Geburtsjahr aus der neuesten Standardliste in data/.

Methode: Peak-Alter wird NICHT als Alter beim Höchstwert je Spieler gemessen —
das Datenfenster (2009 bis heute) schneidet bei Älteren den echten Höchstwert
ab, bei Jungen fehlt der Rest der Karriere. Stattdessen: jährliche
Rating-Veränderung (Januar → Januar) je Lebensalter über alle GMs. Die
aufsummierte mittlere Veränderung ergibt eine typische Karrierekurve; ihr
Maximum ist das typische Peak-Alter. Jahre ohne Veränderung gelten als Jahre
ohne Partien („inaktiv“) und lassen sich ausblenden.

Cache: gm_peak_cache.parquet neben dieser Datei (gitignored wie alle Caches),
wird beim ersten Zugriff aus den beiden Quellen gebaut (~30 s).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from core.fide_list import load_players_from_file
from data_player_history import HISTORY_PATH, MONTH_MAP

_here = Path(__file__).resolve().parent
DATA_DIR = _here.parent / "data"
CACHE_PATH = _here / "gm_peak_cache.parquet"

LEVELS = ["2500–2599", "2600–2699", "2700+"]   # nach Höchstwert im Datenfenster
COHORTS = [1970, 1980, 1990, 2000]              # Geburtsjahrzehnte für den Generationenvergleich
MIN_N = 50                                       # Mindestzahl Spielerjahre je Alter für Kurven
AGE_MIN, AGE_MAX = 12, 72

_cache: pd.DataFrame | None = None


def _newest_list() -> Path:
    best = None
    for fname in os.listdir(DATA_DIR):
        m = re.search(r"([a-z]{3})(\d{2})frl\.zip$", fname)
        if not m or m.group(1) not in MONTH_MAP:
            continue
        d = date(2000 + int(m.group(2)), MONTH_MAP[m.group(1)], 1)
        if best is None or d > best[0]:
            best = (d, DATA_DIR / fname)
    if best is None:
        raise FileNotFoundError(f"Keine FIDE-Liste in {DATA_DIR}")
    return best[1]


def _level(peak: int) -> str:
    if peak >= 2700:
        return "2700+"
    if peak >= 2600:
        return "2600–2699"
    return "2500–2599"


def build_cache() -> pd.DataFrame:
    """Je GM und Jahr: Januar-Rating, Veränderung zum nächsten Januar, Alter."""
    logging.getLogger("core.fide_list").setLevel(logging.WARNING)
    players = pd.DataFrame(load_players_from_file(_newest_list()))
    gm = players[(players["title"] == "GM") & players["birth_year"].notna()][
        ["fide_id", "name", "federation", "sex", "birth_year"]
    ].copy()
    gm["birth_year"] = gm["birth_year"].astype(int)

    hist = ds.dataset(HISTORY_PATH).to_table(
        filter=ds.field("fide_id").isin(pa.array(gm["fide_id"].astype("int32")))
    ).to_pandas()
    peak = hist.groupby("fide_id")["rating"].max().rename("peak_window")

    jan = hist[hist["period"].dt.month == 1].copy()
    jan["year"] = jan["period"].dt.year
    jan = jan.sort_values(["fide_id", "year"])
    jan["next_rating"] = jan.groupby("fide_id")["rating"].shift(-1)
    jan["next_year"] = jan.groupby("fide_id")["year"].shift(-1)
    yr = jan[jan["next_year"] == jan["year"] + 1].copy()

    df = yr.merge(gm, on="fide_id").merge(peak, on="fide_id")
    df["delta"] = (df["next_rating"] - df["rating"]).astype("int16")
    df["age"] = (df["year"] - df["birth_year"]).astype("int16")
    df["active"] = df["delta"] != 0
    df["level"] = df["peak_window"].map(_level)
    df["cohort"] = (df["birth_year"] // 10 * 10).astype("int16")
    out = df[["fide_id", "name", "federation", "sex", "birth_year", "cohort", "year", "age",
              "rating", "delta", "active", "peak_window", "level"]].reset_index(drop=True)
    out.to_parquet(CACHE_PATH, index=False)
    return out


def load_gm_years() -> pd.DataFrame:
    global _cache
    if _cache is None:
        _cache = pd.read_parquet(CACHE_PATH) if CACHE_PATH.exists() else build_cache()
    return _cache


# ---------------------------------------------------------------------------
# Auswertungen (klein genug für Berechnung je Aufruf)
# ---------------------------------------------------------------------------

def age_curve(df: pd.DataFrame, active_only: bool = True, min_n: int = MIN_N) -> pd.DataFrame:
    """Mittlere Jahresveränderung je Alter + aufsummierte Kurve relativ zum Maximum."""
    d = df[df["active"]] if active_only else df
    d = d[(d["age"] >= AGE_MIN) & (d["age"] <= AGE_MAX)]
    a = d.groupby("age")["delta"].agg(n="size", mean="mean", median="median").reset_index()
    a = a[a["n"] >= min_n].copy()
    a["curve"] = a["mean"].cumsum()
    a["curve"] -= a["curve"].max()
    return a


def peak_age(curve: pd.DataFrame) -> int:
    # curve[age] = Rating im Januar nach dem Jahr, in dem der Spieler `age` wird,
    # also ungefähr mit `age` Jahren.
    return int(curve.loc[curve["curve"].idxmax(), "age"])


def points_below_peak(curve: pd.DataFrame, at_age: int) -> int:
    row = curve[curve["age"] == at_age]
    return int(round(row["curve"].iloc[0])) if len(row) else 0


def inactive_share(df: pd.DataFrame) -> pd.DataFrame:
    d = df[(df["age"] >= 15) & (df["age"] <= AGE_MAX)]
    s = d.assign(inactive=~d["active"]).groupby("age").agg(n=("inactive", "size"),
                                                          share=("inactive", "mean")).reset_index()
    return s[s["n"] >= MIN_N]


def cohort_bands(df: pd.DataFrame) -> pd.DataFrame:
    """Mittlere Jahresveränderung (aktive Jahre) je Altersband und Geburtsjahrzehnt."""
    d = df[df["active"] & df["cohort"].isin(COHORTS) & (df["age"] >= 18) & (df["age"] <= 41)]
    bands = pd.cut(d["age"], [17, 21, 25, 29, 33, 37, 41],
                   labels=["18–21", "22–25", "26–29", "30–33", "34–37", "38–41"])
    g = d.groupby([bands, "cohort"], observed=True)["delta"].agg(n="size", mean="mean").reset_index()
    g = g.rename(columns={"age": "band"})
    return g[g["n"] >= MIN_N]


def player_options() -> list[dict]:
    df = load_gm_years()
    p = (df.sort_values("year").groupby("fide_id")
         .agg(name=("name", "first"), federation=("federation", "first"),
              peak=("peak_window", "first"), birth_year=("birth_year", "first"))
         .reset_index().sort_values("peak", ascending=False))
    return [{"label": f"{r.name} ({r.federation}, *{r.birth_year}, max. {r.peak})", "value": int(r.fide_id)}
            for r in p.itertuples()]


def player_by_age(fide_id: int) -> pd.DataFrame:
    """Monatsverlauf eines GMs nach Alter, relativ zu seinem Höchstwert im Datenfenster."""
    df = load_gm_years()
    meta = df[df["fide_id"] == fide_id].iloc[0]
    hist = ds.dataset(HISTORY_PATH).to_table(filter=ds.field("fide_id") == fide_id).to_pandas()
    hist = hist.sort_values("period")
    hist["age"] = hist["period"].dt.year + (hist["period"].dt.month - 1) / 12 - meta["birth_year"]
    hist["rel"] = hist["rating"] - hist["rating"].max()
    hist["name"] = meta["name"]
    return hist


if __name__ == "__main__":
    out = build_cache()
    print(f"{CACHE_PATH.name}: {len(out):,} Spielerjahre, {out['fide_id'].nunique():,} GMs")
