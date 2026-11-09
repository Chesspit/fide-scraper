"""Story A3 „Die Zone 2400–2600: Durchgangsstation oder Endstation?" — Daten.

Quelle ohne DB: Monatsratings aus player_history_cache.parquet (ab 2009), Geschlecht
und Geburtsjahr aus der neuesten Standardliste, Listen 2006–2008 aus data/ nur, um
Rückkehrer zu erkennen.

Einstieg   erste Liste mit Rating ≥ 2400, davor mindestens 12 Monate darunter
           (sonst ist der Einstieg nicht beobachtet). Ausgeschlossen: wer 2006–2008
           schon ≥ 2400 stand, und GMs, die seit 2009 nie 2500 erreicht haben
           (Titel nur mit 2500 möglich, also früher schon einmal oben).
Nach oben  erste Liste ≥ 2600
Nach unten erstmals 6 Listen in Folge < 2400 (kurze Ausrutscher zählen nicht)
Status nach t Jahren nur für Einsteiger, deren Einstieg mindestens t Jahre zurückliegt.

Cache: zone2400_cache.parquet (gitignored). Auf dem VPS liegen keine FIDE-Listen —
Cache lokal bauen (`python data_zone2400.py`) und mit den anderen Caches hochkopieren.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from core.fide_list import load_players_from_file
from data_player_history import HISTORY_PATH, META_PATH, MONTH_MAP

_here = Path(__file__).resolve().parent
DATA_DIR = _here.parent / "data"
CACHE_PATH = _here / "zone2400_cache.parquet"

LOW, HIGH = 2400, 2600
LOOKBACK_MONTHS = 12   # so lange vor dem Einstieg mindestens unter 2400 beobachtet
DOWN_MONTHS = 6        # so viele Listen in Folge unter 2400 = nach unten raus
AGE_GROUPS = ["U18", "18–24", "25+"]
STATES = ["oben", "drin", "unten"]   # Reihenfolge für gestapelte Darstellungen

_cache: pd.DataFrame | None = None


def _list_files() -> list[tuple[date, Path]]:
    out = []
    for fname in os.listdir(DATA_DIR):
        m = re.search(r"([a-z]{3})(\d{2})frl\.zip$", fname)
        if m and m.group(1) in MONTH_MAP:
            out.append((date(2000 + int(m.group(2)), MONTH_MAP[m.group(1)], 1), DATA_DIR / fname))
    return sorted(out)


def build_cache() -> pd.DataFrame:
    """Eine Zeile je beobachtetem Einstieg in die Zone."""
    logging.getLogger("core.fide_list").setLevel(logging.WARNING)
    files = _list_files()
    if not files:
        raise FileNotFoundError(f"Keine FIDE-Listen in {DATA_DIR} — Cache lokal bauen")
    newest = pd.DataFrame(load_players_from_file(files[-1][1]))[
        ["fide_id", "name", "federation", "sex", "birth_year", "title"]]
    before_2009 = {p["fide_id"] for d, f in files if d.year < 2009
                   for p in load_players_from_file(f) if p["std_rating"] >= LOW}

    meta = pd.read_parquet(META_PATH, columns=["fide_id", "peak_rating"])
    ids = meta.loc[meta["peak_rating"] >= LOW, "fide_id"].astype("int32")
    hist = (ds.dataset(HISTORY_PATH).to_table(filter=ds.field("fide_id").isin(pa.array(ids)))
            .to_pandas().sort_values(["fide_id", "period"]))

    rows = []
    for fid, g in hist.groupby("fide_id", sort=False):
        r, p = g["rating"].to_numpy(), g["period"].to_numpy()
        above = np.flatnonzero(r >= LOW)
        if not len(above) or above[0] < LOOKBACK_MONTHS:
            continue
        e = above[0]
        up = np.flatnonzero(r[e:] >= HIGH)
        below = r < LOW
        down = next((i for i in range(e + 1, len(r) - DOWN_MONTHS + 1)
                     if below[i:i + DOWN_MONTHS].all()), None)
        back = None
        if down is not None:
            again = np.flatnonzero(r[down:] >= LOW)
            back = p[down + again[0]] if len(again) else None
        rows.append({
            "fide_id": int(fid), "entry": p[e], "last": p[-1],
            "up": p[e + up[0]] if len(up) else None,
            "down": p[down] if down is not None else None,
            "back": back, "peak": int(r.max()), "last_rating": int(r[-1]),
            "frozen_12m": len(r) >= 12 and len(set(r[-12:])) == 1,
        })
    s = pd.DataFrame(rows)
    for c in ("entry", "last", "up", "down", "back"):
        s[c] = pd.to_datetime(s[c])
    s = s.merge(newest, on="fide_id", how="left")
    returner = s["fide_id"].isin(before_2009) | ((s["title"] == "GM") & (s["peak"] < 2500))
    s = s[~returner].copy()
    s["age_entry"] = s["entry"].dt.year - s["birth_year"]
    s["age_group"] = pd.cut(s["age_entry"], [0, 17, 24, 120], labels=AGE_GROUPS).astype(str)
    s.loc[s["age_entry"].isna(), "age_group"] = None
    s.to_parquet(CACHE_PATH, index=False)
    return s


def load_entries() -> pd.DataFrame:
    global _cache
    if _cache is None:
        _cache = pd.read_parquet(CACHE_PATH) if CACHE_PATH.exists() else build_cache()
    return _cache


def last_period() -> pd.Timestamp:
    return load_entries()["last"].max()


# ---------------------------------------------------------------------------
# Auswertungen
# ---------------------------------------------------------------------------

def state_after(s: pd.DataFrame, years: int) -> dict:
    """Anteile oben/drin/unten genau `years` Jahre nach dem Einstieg (erstes Verlassen zählt)."""
    sub = s[s["entry"] <= last_period() - pd.DateOffset(years=years)]
    if sub.empty:
        return {"n": 0, **{k: np.nan for k in STATES}}
    lim = sub["entry"] + pd.DateOffset(years=years)
    t_up = sub["up"].where(sub["up"] <= lim)
    t_dn = sub["down"].where(sub["down"] <= lim)
    up = t_up.notna() & (t_dn.isna() | (t_up < t_dn))
    dn = t_dn.notna() & ~up
    return {"n": len(sub), "oben": up.mean(), "unten": dn.mean(), "drin": (~up & ~dn).mean()}


def state_curve(s: pd.DataFrame, max_years: int = 12, min_n: int = 30) -> pd.DataFrame:
    rows = [{"years": t, **state_after(s, t)} for t in range(0, max_years + 1)]
    df = pd.DataFrame(rows)
    return df[df["n"] >= min_n]


def peak_shares(s: pd.DataFrame) -> pd.DataFrame:
    bins = [LOW - 1, 2449, 2499, 2549, 2599, 4000]
    labels = ["2400–2449", "2450–2499", "2500–2549", "2550–2599", "2600+"]
    c = pd.cut(s["peak"], bins, labels=labels).value_counts(sort=False)
    return pd.DataFrame({"band": labels, "n": c.values, "share": c.values / c.sum()})


def facts(s: pd.DataFrame) -> dict:
    dn, up = s[s["down"].notna()], s[s["up"].notna()]
    one = state_after(s, 1)
    five = state_after(s, 5)
    return {
        "n": len(s),
        "first_year": int(s["entry"].dt.year.min()),
        "down_1y": one["unten"], "down_5y": five["unten"], "up_5y": five["oben"],
        "months_to_down": float(((dn["down"] - dn["entry"]).dt.days / 30.44).median()),
        "back_share": float(dn["back"].notna().mean()),
        "n_up": len(up),
        "years_to_up": float(((up["up"] - up["entry"]).dt.days / 365.25).median()),
        "age_up": float(up["age_entry"].median()),
        "peak_lt_2450": float((s["peak"] < 2450).mean()),
    }


if __name__ == "__main__":
    out = build_cache()
    print(f"{CACHE_PATH.name}: {len(out):,} Einstiege")
