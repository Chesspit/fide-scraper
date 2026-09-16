"""Shared setup for notebooks: DB connection, styling, data loading."""

import warnings
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import dotenv_values

warnings.filterwarnings(
    "ignore",
    message="pandas only supports SQLAlchemy connectable",
    category=UserWarning,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_conn():
    cfg = dotenv_values(PROJECT_ROOT / ".env.notebook")
    conn = psycopg2.connect(cfg["DATABASE_URL"])
    # VPS-Container hat ein sehr kleines /dev/shm; parallele Hash-Joins/-Sorts
    # laufen dort in "could not resize shared memory segment ... No space left
    # on device" (beobachtet bei größeren Ad-hoc-Joins, z.B. Notebook 15).
    with conn.cursor() as cur:
        cur.execute("SET max_parallel_workers_per_gather = 0")
    conn.commit()
    return conn


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
