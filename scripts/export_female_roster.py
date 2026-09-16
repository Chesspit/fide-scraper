"""Export die kumulative Top-N-Frauen-Roster-CSV (Jahresend-Elo 2016-2025).

Liefert für jede Spielerin, die je zu einem Jahresende (Dez. 2016-2025) zu den
aktiven Top-N-Frauen nach `published_rating` gehörte: FIDE-ID, Name,
Aufnahmejahr (erstes Jahr in den Top-N) und die Jahresend-Elo für alle zehn
Jahre (NaN = kein Rating in diesem Jahr).

Pendant zur Top-40-Roster-CSV (notebooks/top40_female_roster_2016-2025.csv,
seinerzeit ad hoc erzeugt) — hier als wiederverwendbares Skript, parametrisiert
über --top-n.

Nutzung:
    .venv/bin/python scripts/export_female_roster.py --top-n 20 \
        --out notebooks/top20_female_roster_2016-2025.csv

Mit --include-inactive liefert der Export zusätzlich Spielerinnen, die zu
irgendeinem Jahresende historisch in den Top-N standen, aber inzwischen
`active = FALSE` sind und deshalb in der regulären (Default-)Liste fehlen —
für einen Übersichts-Vergleich "wer wurde durch den Aktiv-Filter rausgefiltert".
Die CSV bekommt dann eine zusätzliche Spalte `active` (0/1, aktueller Status).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "notebooks"))
from _setup import get_conn  # noqa: E402

YEARS = list(range(2016, 2026))
PERIODS = [f"{y}-12-01" for y in YEARS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, required=True, help="Rang-Cutoff je Jahresende, z.B. 20 oder 40")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--include-inactive",
        action="store_true",
        help="Auch inzwischen inaktive Spielerinnen mitliefern, die historisch in den Top-N standen",
    )
    args = ap.parse_args()

    periods_sql = ",".join(f"'{p}'" for p in PERIODS)
    # Der Rang (rnk) wird immer über ALLE Spielerinnen je Periode berechnet
    # (aktiv + inaktiv), das entspricht der historisch tatsächlichen Platzierung.
    # Der active-Filter greift erst danach, beim Auswählen, wer in die Liste kommt.
    active_filter = "" if args.include_inactive else "AND active"
    row_filter = "" if args.include_inactive else "WHERE fy.active"
    sql = f"""
        WITH female_years AS (
            SELECT rh.fide_id, p.name, p.active, rh.period, rh.published_rating,
                   ROW_NUMBER() OVER (PARTITION BY rh.period ORDER BY rh.published_rating DESC) AS rnk
            FROM rating_history rh
            JOIN players p ON p.fide_id = rh.fide_id
            WHERE p.sex = 'F'
              AND rh.published_rating IS NOT NULL
              AND rh.period IN ({periods_sql})
        ),
        top_ids AS (
            SELECT DISTINCT fide_id FROM female_years WHERE rnk <= {args.top_n} {active_filter}
        )
        SELECT fy.fide_id, fy.name, fy.active, fy.period, fy.published_rating, fy.rnk
        FROM female_years fy
        JOIN top_ids t ON t.fide_id = fy.fide_id
        {row_filter}
    """
    conn = get_conn()
    raw = pd.read_sql(sql, conn)
    conn.close()

    raw["year"] = pd.to_datetime(raw["period"]).dt.year
    year_added = (
        raw[raw["rnk"] <= args.top_n].groupby("fide_id")["year"].min().rename("year_added")
    )
    names = raw.drop_duplicates("fide_id").set_index("fide_id")["name"]
    ratings = raw.pivot(index="fide_id", columns="year", values="published_rating")
    ratings.columns = [str(c) for c in ratings.columns]
    for y in YEARS:
        if str(y) not in ratings.columns:
            ratings[str(y)] = pd.NA
    ratings = ratings[[str(y) for y in YEARS]]

    cols = [names, year_added, ratings]
    if args.include_inactive:
        active = raw.drop_duplicates("fide_id").set_index("fide_id")["active"]
        cols.append(active)
    roster = pd.concat(cols, axis=1).reset_index()
    roster["year_added"] = roster["year_added"].astype(int)
    for y in YEARS:
        roster[str(y)] = pd.to_numeric(roster[str(y)], errors="coerce").astype("Int64")

    # Sortierung wie im bestehenden Export: Aufnahmejahr aufsteigend, innerhalb
    # des Jahrgangs absteigend nach Elo im Aufnahmejahr.
    def entry_rating(row):
        return row[str(row["year_added"])]
    roster["_entry_rating"] = roster.apply(entry_rating, axis=1)
    roster = roster.sort_values(
        ["year_added", "_entry_rating"], ascending=[True, False]
    ).drop(columns="_entry_rating").reset_index(drop=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    roster.to_csv(out_path, index=False)
    print(f"wrote {out_path} ({len(roster)} Spielerinnen)")
    print(roster["year_added"].value_counts().sort_index())
    if args.include_inactive:
        n_inactive = int((~roster["active"]).sum())
        print(f"davon inaktiv (nicht in der regulären Liste): {n_inactive}")


if __name__ == "__main__":
    main()
