#!/usr/bin/env python3
"""Export DB snapshots as compressed Parquet files for frontend use."""

import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = "postgresql+psycopg2://fide:nimzo194.@localhost:5434/fidedb"
OUTPUT_DIR = Path(__file__).parent / "frontend_data"
OUTPUT_DIR.mkdir(exist_ok=True)

engine = create_engine(DB_URL, connect_args={"connect_timeout": 30})

EXPORTS = [
    (
        "players_export",
        """
        SELECT fide_id, name, federation, sex, title, birth_year,
               std_rating, analysis_group, swiss_2026, active
        FROM players
        WHERE analysis_group IS NOT NULL OR swiss_2026 = TRUE
        """,
        {},
    ),
    (
        "games_export",
        """
        SELECT fide_id,
               period::date,
               opponent_name,
               opponent_rating,
               opponent_sex,
               result,
               rating_change_weighted,
               color,
               tournament_type,
               expected_score,
               over_performance
        FROM game_results
        """,
        {
            "opponent_rating": "Int16",
            "result": "category",
            "opponent_sex": "category",
            "color": "category",
            "tournament_type": "category",
        },
    ),
    (
        "history_export",
        """
        SELECT rh.fide_id, rh.period::date, rh.std_rating, rh.num_games
        FROM rating_history rh
        JOIN players p USING (fide_id)
        WHERE p.analysis_group IS NOT NULL OR p.swiss_2026 = TRUE
        """,
        {},
    ),
]

with engine.connect() as conn:
    for name, query, dtypes in EXPORTS:
        print(f"Exportiere {name}...", flush=True)
        df = pd.read_sql(text(query), conn)

        for col, dtype in dtypes.items():
            if col in df.columns:
                df[col] = df[col].astype(dtype)

        path = OUTPUT_DIR / f"{name}.parquet"
        df.to_parquet(path, index=False, compression="zstd", engine="pyarrow")
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  → {len(df):,} Zeilen, {size_mb:.1f} MB — {path.name}")

print("\nFertig. Dateien in frontend_data/:")
for f in sorted(OUTPUT_DIR.glob("*.parquet")):
    print(f"  {f.name}  ({f.stat().st_size / 1024 / 1024:.1f} MB)")
