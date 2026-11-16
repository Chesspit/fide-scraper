"""Data loading for the FIDE dashboard."""
import logging
import os
import threading
import time

import pandas as pd

from core.db import connect

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
log = logging.getLogger(__name__)


def _connect():
    return connect(role="reader")


def _fetch(sql: str, params=None) -> pd.DataFrame:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


HISTORY_CACHE_PATH = os.path.join(os.path.dirname(__file__), "history2700_cache.parquet")
HISTORY_CACHE_MAX_AGE = 24 * 3600   # Sekunden; danach im Hintergrund erneuern
HISTORY_COLUMNS = {"fide_id", "name", "federation", "birth_year", "period", "rating"}
_refresh_lock = threading.Lock()


def _query_2700_history() -> pd.DataFrame:
    """Rating-Verlauf aller Spieler, die seit 2009 je >= 2700 hatten — aus der DB."""
    # Step 1: fast ID lookup via partial index idx_rating_history_pub2700 (Migration 025)
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
    return df


def _write_history_cache(df: pd.DataFrame) -> None:
    """Atomar schreiben. Scheitert still, wo der Ordner nur lesbar ist
    (öffentliche Edition) — dann bleibt es beim Ergebnis im Speicher."""
    tmp = f"{HISTORY_CACHE_PATH}.{os.getpid()}.tmp"
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, HISTORY_CACHE_PATH)
    except OSError as exc:
        log.info("history2700_cache.parquet nicht geschrieben: %s", exc)
        try:
            os.remove(tmp)
        except OSError:
            pass


def _read_history_cache() -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(HISTORY_CACHE_PATH)
    except (OSError, ValueError):
        return None
    if HISTORY_COLUMNS - set(df.columns) or df.empty:
        return None   # Cache aus anderer Code-Version → neu aus der DB
    return df


def _refresh_history() -> None:
    """Hintergrund: Verlauf neu aus der DB holen, Speicher und Datei ersetzen."""
    if not _refresh_lock.acquire(blocking=False):
        return   # läuft schon
    try:
        df = _query_2700_history()
        _cache["history"] = df
        _write_history_cache(df)
    except Exception as exc:   # DB weg, Zeitgrenze … — alter Stand bleibt gültig
        log.warning("Aktualisierung history2700 fehlgeschlagen: %s", exc)
        _cache["history_checked"] = time.time()   # nicht bei jedem Aufruf neu versuchen
    finally:
        _refresh_lock.release()


def load_2700_history() -> pd.DataFrame:
    """Load rating history for all players who ever had ELO >= 2700 since 2009.

    Die Abfrage braucht nach einem Neustart je nach DB-Last 0,2 bis über 40 s
    (16.11.2026: Seite /c blieb so lange leer). Deshalb liegt das Ergebnis als
    Parquet-Datei neben den anderen Caches: ein frischer Prozess liefert sie
    sofort aus und erneuert sie im Hintergrund, wenn sie älter als ein Tag ist.
    Ohne Datei (erster Start) wird einmal direkt aus der DB geladen.
    """
    if "history" in _cache:
        df = _cache["history"]
    else:
        df = _read_history_cache()
        if df is None:
            df = _query_2700_history()
            _cache["history"] = df
            _cache["history_checked"] = time.time()
            _write_history_cache(df)
            return df
        _cache["history"] = df
        try:
            _cache["history_checked"] = os.path.getmtime(HISTORY_CACHE_PATH)
        except OSError:
            _cache["history_checked"] = 0.0

    if time.time() - _cache.get("history_checked", 0.0) > HISTORY_CACHE_MAX_AGE:
        _cache["history_checked"] = time.time()
        threading.Thread(target=_refresh_history, name="history2700-refresh",
                         daemon=True).start()
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
