"""Story E2 „Die Pause und ihre Folgen" — lädt die vorab gebauten Aggregate.

Gebaut von scripts/analysis/build_covid_story.py (braucht FIDE-Listen + einen
Partien-Export, beides nur lokal). Auf dem VPS liegen nur die vier kleinen
Parquet-Dateien covid_{activity,returns,flows,series}.parquet neben dieser Datei.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

_here = Path(__file__).resolve().parent

AGE_ORDER = ["U15", "15–19", "20–39", "40–59", "60+"]
RATING_ORDER = ["<1400", "1400–1599", "1600–1799", "1800–1999", "2000–2199", "2200+"]
PHASE_ORDER = ["Vor der Pause", "Pause", "Rückkehr", "Welle", "Nach der Korrektur"]
PHASE_SPAN = {
    "Vor der Pause": "Jan–Mär 2020",
    "Pause": "Apr 2020–Jun 2021",
    "Rückkehr": "Jul 2021–Dez 2022",
    "Welle": "Jan 2023–Mär 2024",
    "Nach der Korrektur": "ab Apr 2024",
}
DIM_LABEL = {"age": "Alter", "rating": "Rating"}


def order(dim: str) -> list[str]:
    return AGE_ORDER if dim == "age" else RATING_ORDER


@lru_cache(maxsize=None)
def load(name: str) -> pd.DataFrame:
    return pd.read_parquet(_here / f"covid_{name}.parquet")


def activity() -> pd.DataFrame:
    return load("activity")


def returns() -> pd.DataFrame:
    return load("returns")


def flows() -> pd.DataFrame:
    f = load("flows").copy()
    f["per_year"] = f["sum"] / f["months"] * 12
    return f


def series() -> pd.DataFrame:
    s = load("series").copy()
    s["date"] = pd.to_datetime(s["half"].str[:4] + "-" + s["half"].str[-1].map({"1": "04", "2": "10"}) + "-01")
    return s


def flow_value(dim: str, phase: str, own: str, opp: str, col: str = "mean") -> float:
    f = flows()
    row = f[(f["dim"] == dim) & (f["phase"] == phase) & (f["own"] == own) & (f["opp"] == opp)]
    return float(row[col].iloc[0]) if len(row) else float("nan")


def return_share(dim: str, group: str, cohort: str, months: int = 33) -> float:
    r = returns()
    row = r[(r["dim"] == dim) & (r["group"] == group) & (r["cohort"] == cohort) & (r["months"] == months)]
    return float(row["share"].iloc[0]) if len(row) else float("nan")
