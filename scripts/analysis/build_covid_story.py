#!/usr/bin/env python3
"""Daten für die Story „Die Pause und ihre Folgen“ (E2, Frontend-Seite /pause).

Baut kleine Aggregat-Tabellen nach frontend/covid_*.parquet (gitignored wie alle
Frontend-Caches; auf den VPS hochkopieren). Eingaben:

1. FIDE-Standardlisten ab 2016 in data/ (Aktivität = Partien je Liste)
2. Partien ab 2020 aus game_results als CSV(.gz) — einmalig exportieren, z. B. auf
   dem VPS (ein Lesezugriff, ~2 min):

       docker exec -i fide-db psql -U fide -d fidedb <<'SQL' | gzip > games_2020.csv.gz
       \\copy (select fide_id, period, opponent_fide_id, opponent_name, opponent_federation,
              opponent_rating, result, rating_change_weighted from game_results
              where period >= date '2020-01-01') to stdout with csv header
       SQL

3. frontend/player_meta_cache.parquet (Name + Föderation für die Gegnerzuordnung)

    python scripts/analysis/build_covid_story.py --games ~/Downloads/games_2020.csv.gz

Gegnerzuordnung: opponent_fide_id fehlt bei ~98 % der Partien. Zugeordnet wird über
gleichen Namen und ein Rating, das exakt einer Liste 1–3 Monate vor der Periode
entspricht, oder den einzigen Namensträger mit ≤ 15 Punkten Abweichung. Prüfung an den Partien mit bekannter ID:
99,8 % richtig, ~80 % der Partien zugeordnet. Für Ratinggruppen ist keine
Zuordnung nötig (Gegner-Rating steht in jeder Partie).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import PROJECT_ROOT
from core.fide_list import load_players_from_file

DATA_DIR = PROJECT_ROOT / "data"
OUT_DIR = PROJECT_ROOT / "frontend"
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}

AGE_BINS, AGE_LABELS = [0, 14, 19, 39, 59, 120], ["U15", "15–19", "20–39", "40–59", "60+"]
RATING_BINS = [0, 1399, 1599, 1799, 1999, 2199, 4000]
RATING_LABELS = ["<1400", "1400–1599", "1600–1799", "1800–1999", "2000–2199", "2200+"]

# Phasen nach Periode der Partie (Periode = Monat der Liste, in die sie einging)
PHASES = [
    ("2020-01-01", "2020-04-01", "Vor der Pause", "Jan–Mär 2020"),
    ("2020-04-01", "2021-07-01", "Pause", "Apr 2020–Jun 2021"),
    ("2021-07-01", "2023-01-01", "Rückkehr", "Jul 2021–Dez 2022"),
    ("2023-01-01", "2024-04-01", "Welle", "Jan 2023–Mär 2024"),
    ("2024-04-01", "2100-01-01", "Nach der Korrektur", "ab Apr 2024"),
]

# Kohorten für die Rückkehr: aktiv in den 14 Monaten bis zum Stichtag
COHORTS = {"normal": ("2016-02-01", "2017-03-01"), "corona": ("2019-02-01", "2020-03-01")}

log = logging.getLogger("build_covid_story")


def _lists(since: date) -> list[tuple[date, Path]]:
    out = []
    for fname in os.listdir(DATA_DIR):
        m = re.search(r"([a-z]{3})(\d{2})frl\.zip$", fname)
        if m and m.group(1) in MONTHS:
            d = date(2000 + int(m.group(2)), MONTHS[m.group(1)], 1)
            if d >= since:
                out.append((d, DATA_DIR / fname))
    return sorted(out)


def load_lists() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bewertete Spieler je Liste ab 2016 (Rating, Partien) + Stammdaten der neuesten Liste."""
    files = _lists(date(2016, 1, 1))
    frames = []
    for d, path in files:
        df = pd.DataFrame(load_players_from_file(path))[["fide_id", "std_rating", "std_games"]]
        df = df[df["std_rating"] > 0].copy()
        df["std_games"] = df["std_games"].fillna(0).astype("int16")
        df["period"] = pd.Timestamp(d)
        frames.append(df.astype({"fide_id": "int32", "std_rating": "int16"}))
        log.info("Liste %s: %d bewertet", d, len(df))
    lists = pd.concat(frames, ignore_index=True)
    people = pd.DataFrame(load_players_from_file(files[-1][1]))[["fide_id", "birth_year", "sex"]]
    return lists, people


def _age_group(age: pd.Series) -> pd.Series:
    return pd.cut(age, AGE_BINS, labels=AGE_LABELS)


def _rating_group(r: pd.Series) -> pd.Series:
    return pd.cut(r.astype("float"), RATING_BINS, labels=RATING_LABELS)


# ---------------------------------------------------------------------------
# Teil 1: Aktivität und Rückkehr
# ---------------------------------------------------------------------------

def activity(lists: pd.DataFrame, by: pd.Series) -> pd.DataFrame:
    """Spieler mit Partien je Liste, gesamt und je Gruppe, Index 2019 = 100."""
    act = lists[(lists["std_games"] > 0) & (lists["period"] >= "2018-01-01")].copy()
    act["age"] = _age_group(act["period"].dt.year - act["fide_id"].map(by))
    act["rating"] = _rating_group(act["std_rating"])
    rows = []
    for dim, col in (("all", None), ("age", "age"), ("rating", "rating")):
        keys = ["period"] + ([col] if col else [])
        n = act.groupby(keys, observed=True).size().rename("n").reset_index()
        n["group"] = n[col].astype(str) if col else "alle"
        n["dim"] = dim
        rows.append(n[["dim", "group", "period", "n"]])
    out = pd.concat(rows, ignore_index=True)
    base = out[out["period"].dt.year == 2019].groupby(["dim", "group"])["n"].mean().rename("base")
    out = out.join(base, on=["dim", "group"])
    out["index"] = out["n"] / out["base"] * 100
    return out


def returns(lists: pd.DataFrame, by: pd.Series) -> pd.DataFrame:
    """Anteil der Aktiven, die nach dem Stichtag wieder gewertet gespielt haben, je Monat."""
    act = lists[lists["std_games"] > 0][["fide_id", "period", "std_rating"]]
    last = act["period"].max()
    rows = []
    for cohort, (b0, b1) in COHORTS.items():
        cut = pd.Timestamp(b1)
        base = (act[(act["period"] >= b0) & (act["period"] <= cut)]
                .groupby("fide_id")["std_rating"].last().reset_index())
        back = act[act["period"] > cut].groupby("fide_id")["period"].min()
        base["months"] = ((base["fide_id"].map(back) - cut).dt.days / 30.44).round()
        base["age"] = _age_group(cut.year - base["fide_id"].map(by))
        base["rating"] = _rating_group(base["std_rating"])
        horizon = int((last - cut).days / 30.44)
        if cohort == "normal":
            horizon = min(horizon, 33)   # danach beginnt bei dieser Kohorte die Pause
        for dim, col in (("all", None), ("age", "age"), ("rating", "rating")):
            groups = [("alle", base)] if col is None else [
                (str(k), d) for k, d in base.groupby(col, observed=True)]
            for gname, d in groups:
                m = d["months"].to_numpy()
                for k in range(0, horizon + 1):
                    rows.append({"cohort": cohort, "dim": dim, "group": gname, "months": k,
                                 "n": len(d), "share": float(np.mean(m <= k))})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Teil 2: Punkteströme aus den Partien
# ---------------------------------------------------------------------------

def load_games(path: Path) -> pd.DataFrame:
    with pd.io.common.get_handle(path, "r", compression="infer") as h:
        skip = 0 if h.handle.readline().startswith("fide_id") else 1   # psql schreibt evtl. „SET“ davor
    g = pd.read_csv(path, skiprows=skip, usecols=["fide_id", "period", "opponent_fide_id", "opponent_name",
                                                  "opponent_federation", "opponent_rating",
                                                  "rating_change_weighted"],
                    dtype={"fide_id": "int32", "opponent_fide_id": "Int32", "opponent_rating": "float32",
                           "opponent_federation": "string", "opponent_name": "string"})
    g["period"] = pd.to_datetime(g["period"])
    g["d"] = g.pop("rating_change_weighted").astype("float32")
    return g


def resolve_opponents(g: pd.DataFrame, lists: pd.DataFrame) -> pd.Series:
    """fide_id des Gegners je Partie allein aus Name + Rating (NaN wenn nicht eindeutig)."""
    meta = pd.read_parquet(OUT_DIR / "player_meta_cache.parquet", columns=["fide_id", "name"])
    meta["name"] = meta["name"].astype("string")
    keys = ["period", "opponent_name", "opponent_federation", "opponent_rating"]
    u = g[keys].drop_duplicates().reset_index(drop=True)
    u["uid"] = np.arange(len(u))
    c = u.merge(meta.rename(columns={"name": "opponent_name", "fide_id": "cand"}), on="opponent_name")
    rated = lists[["fide_id", "period", "std_rating"]].rename(columns={"fide_id": "cand", "period": "p"})
    best = None
    for lag in (1, 2, 3):
        x = c.assign(p=c["period"] - pd.DateOffset(months=lag)).merge(rated, on=["cand", "p"], how="left")
        diff = (x["std_rating"].astype("float") - x["opponent_rating"].astype("float")).abs()
        best = diff if best is None else np.fmin(best, diff)
    c["diff"] = best.to_numpy()
    # eindeutig: genau ein Kandidat mit exakt passendem Rating, oder der einzige
    # Namensträger mit höchstens 15 Punkten Abweichung (Turnier über Monatsgrenzen)
    exact = c[c["diff"] == 0].groupby("uid")["cand"].agg(["first", "size"])
    by_exact = exact[exact["size"] == 1]["first"]
    single = c.groupby("uid").agg(cand=("cand", "first"), n=("cand", "size"), diff=("diff", "min"))
    by_single = single[(single["n"] == 1) & (single["diff"] <= 15)]["cand"]
    u["opp"] = u["uid"].map(by_exact).fillna(u["uid"].map(by_single))
    return g[keys].merge(u[keys + ["opp"]], on=keys, how="left")["opp"].to_numpy()


def _phase(period: pd.Series) -> pd.Series:
    out = pd.Series(pd.NA, index=period.index, dtype="string")
    for start, end, name, _ in PHASES:
        out[(period >= start) & (period < end)] = name
    return out


def flows(g: pd.DataFrame, lists: pd.DataFrame, by: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    own = lists[["fide_id", "period", "std_rating"]].assign(period=lambda d: d["period"] + pd.DateOffset(months=1))
    g = g.merge(own.rename(columns={"std_rating": "own_rating"}), on=["fide_id", "period"], how="left")
    g["age"] = _age_group(g["period"].dt.year - g["fide_id"].map(by))
    g["opp_age"] = _age_group(g["period"].dt.year - g["opp"].map(by))
    g["rating"] = _rating_group(g["own_rating"])
    g["opp_rating"] = _rating_group(g["opponent_rating"])
    g["phase"] = _phase(g["period"])
    g["half"] = g["period"].dt.year.astype(str) + "-H" + np.where(g["period"].dt.month <= 6, "1", "2")

    mats, series = [], []
    for dim, a, b in (("age", "age", "opp_age"), ("rating", "rating", "opp_rating")):
        m = (g.groupby(["phase", a, b], observed=True)["d"].agg(["mean", "sum", "size"])
             .reset_index().rename(columns={a: "own", b: "opp", "size": "n"}))
        m["dim"] = dim
        mats.append(m)
        s = (g.groupby(["half", a], observed=True)["d"].agg(["mean", "sum", "size"])
             .reset_index().rename(columns={a: "group", "size": "n"}))
        s["dim"] = dim
        series.append(s)
    mat = pd.concat(mats, ignore_index=True)
    # Dauer je Phase in Monaten (Perioden mit Partien) → „Summe pro Jahr“ vergleichbar
    mat["months"] = mat["phase"].map(g.groupby("phase", observed=True)["period"].nunique())
    mat[["own", "opp"]] = mat[["own", "opp"]].astype(str)
    ser = pd.concat(series, ignore_index=True)
    ser["group"] = ser["group"].astype(str)
    log.info("Gegneralter bekannt: %.1f %% der Partien", g["opp_age"].notna().mean() * 100)
    return mat, ser


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=Path, required=True, help="CSV(.gz) der Partien ab 2020")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("core.fide_list").setLevel(logging.WARNING)

    lists, people = load_lists()
    by = people.dropna(subset=["birth_year"]).set_index("fide_id")["birth_year"]

    activity(lists, by).to_parquet(OUT_DIR / "covid_activity.parquet", index=False)
    returns(lists, by).to_parquet(OUT_DIR / "covid_returns.parquet", index=False)

    g = load_games(args.games)
    log.info("%d Partien geladen", len(g))
    matched = pd.Series(resolve_opponents(g, lists), index=g.index)
    known = g["opponent_fide_id"].astype("float")
    check = known.notna() & matched.notna()
    log.info("Prüfung an %d Partien mit bekannter Gegner-ID: %.2f %% richtig zugeordnet",
             check.sum(), (matched[check] == known[check]).mean() * 100)
    g["opp"] = known.fillna(matched)
    log.info("Gegner zugeordnet: %.1f %% der Partien", g["opp"].notna().mean() * 100)
    mat, ser = flows(g, lists, by)
    mat.to_parquet(OUT_DIR / "covid_flows.parquet", index=False)
    ser.to_parquet(OUT_DIR / "covid_series.parquet", index=False)
    log.info("fertig: frontend/covid_{activity,returns,flows,series}.parquet")


if __name__ == "__main__":
    main()
