"""Data loading for the FIDE dashboard."""
import os
import pandas as pd
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://fide:nimzo194.@localhost:5434/fidedb")

DEFAULT_PLAYER_IDS = [
    2805677,   # Gelfand, Boris (1968)
    5000017,   # Anand, Viswanathan (1969)
    4102142,   # Svidler, Peter (1976)
    1503014,   # Carlsen, Magnus (1990)
    1170546,   # Duda, Jan-Krzysztof (1998)
    46616543,  # Gukesh D (2006)
]

MAX_HIGHLIGHTS = 12

HIGHLIGHT_COLORS = [
    "#D62728",  # red
    "#E8602C",  # orange-red
    "#F5933A",  # orange
    "#E6B800",  # dark yellow
    "#6B9E36",  # yellow-green
    "#2CA02C",  # green
    "#17A89C",  # teal
    "#00BCD4",  # cyan
    "#2196F3",  # blue
    "#3949AB",  # indigo
    "#7B1FA2",  # purple
    "#C2185B",  # pink
]

_cache: dict = {}


def _connect():
    return psycopg2.connect(DATABASE_URL)


def _fetch(sql: str, params=None) -> pd.DataFrame:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


def load_2700_history() -> pd.DataFrame:
    """Load rating history for all players who ever had ELO >= 2700 since 2009."""
    if "history" in _cache:
        return _cache["history"]

    # Step 1: fast ID lookup via published_rating index
    id_df = _fetch(
        "SELECT DISTINCT fide_id FROM rating_history "
        "WHERE published_rating >= 2700 AND period >= %s",
        ("2009-01-01",),
    )
    fide_ids = id_df["fide_id"].tolist()

    # Step 2: load their full history including birth_year
    df = _fetch(
        """
        SELECT rh.fide_id, p.name, p.federation, p.birth_year, rh.period,
               COALESCE(rh.published_rating, rh.std_rating) AS rating
        FROM rating_history rh
        JOIN players p ON p.fide_id = rh.fide_id
        WHERE rh.fide_id = ANY(%s)
          AND rh.period >= %s
          AND COALESCE(rh.published_rating, rh.std_rating) IS NOT NULL
        ORDER BY p.name, rh.period
        """,
        (fide_ids, "2008-12-01"),
    )
    df["period"] = pd.to_datetime(df["period"])
    df["rating"] = pd.to_numeric(df["rating"])
    df["birth_year"] = pd.to_numeric(df["birth_year"], errors="coerce")
    _cache["history"] = df
    return df


def get_player_options() -> list[dict]:
    df = load_2700_history()
    players = (
        df[["fide_id", "name", "federation"]]
        .drop_duplicates("fide_id")
        .sort_values("name")
    )
    return [
        {"label": f"{row['name']} ({row['federation']})", "value": int(row["fide_id"])}
        for _, row in players.iterrows()
    ]
