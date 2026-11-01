"""Shared setup for notebooks: DB connection, styling, data loading."""

import warnings

import pandas as pd

from core.config import PROJECT_ROOT  # noqa: F401
from core.db import connect_analysis
from core.elo import ELO_BAND_WIDTH, elo_band, elo_band_floor  # noqa: F401

warnings.filterwarnings(
    "ignore",
    message="pandas only supports SQLAlchemy connectable",
    category=UserWarning,
)


def get_conn():
    return connect_analysis()


def load_view(name: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(f"SELECT * FROM {name}", conn)


def load_query(sql: str, params=None) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def apply_style():
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set_theme(style="whitegrid")
    plt.rcParams["figure.figsize"] = (9, 5)
    plt.rcParams["figure.dpi"] = 100


GROUP_PALETTE = {"female_top": "#c0587e", "male_control": "#4a7ab5"}
GROUP_ORDER = ["female_top", "male_control"]


# ── ELO-Bänder ───────────────────────────────────────────────────────────────
# elo_band/elo_band_floor liegen seit Phase 2 in core/elo.py (Spiegelbild von
# fn_elo_band() aus migrations/017) und werden oben re-exportiert, damit die
# Notebooks unverändert "from _setup import elo_band" schreiben können.
