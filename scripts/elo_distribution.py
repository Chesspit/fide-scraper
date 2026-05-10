#!/usr/bin/env python3
"""ELO-Verteilungstabelle aus FIDE-TXT erstellen (nach Ländern und ELO-Ranges)."""

import zipfile
import re
import sys
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "elo_distribution_dec2025.md"

FEDERATIONS = {
    "Welt": None,   # None = alle
    "GER":  "GER",
    "SUI":  "SUI",
    "AUT":  "AUT",
}

RANGE_MIN = 1000
RANGE_MAX = 3100
STEP = 100
ANCHOR = 2000  # Ranges zentriert um 2000


def detect_rating_col(header: str) -> tuple[int, int]:
    """Gibt (start, end) der Rating-Spalte zurück."""
    for marker in ["SRtng", "DEC25", "NOV25", "OCT25"]:
        pos = header.find(marker)
        if pos >= 0:
            return pos, pos + 5
    m = re.search(r"[A-Z]{3}\d{2}", header)
    if m:
        return m.start(), m.start() + 5
    raise ValueError(f"Rating-Spalte nicht gefunden: {header!r}")


def detect_fed_col(header: str) -> tuple[int, int]:
    pos = header.find("Fed")
    if pos < 0:
        raise ValueError("Fed-Spalte nicht gefunden")
    return pos, pos + 3


def load_zip(path: Path) -> list[dict]:
    players = []
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as f:
            header = f.readline().decode("latin-1")
            r_start, r_end = detect_rating_col(header)
            f_start, f_end = detect_fed_col(header)
            for raw in f:
                line = raw.decode("latin-1").rstrip("\n\r")
                if len(line) < r_end:
                    continue
                try:
                    rating = int(line[r_start:r_end].strip())
                except ValueError:
                    continue
                if rating <= 0:
                    continue
                fed = line[f_start:f_end].strip()
                players.append({"rating": rating, "fed": fed})
    return players


def elo_range_label(lo: int) -> str:
    return f"{lo}–{lo + STEP}"


def build_table(players: list[dict]) -> str:
    # Alle Ranges von RANGE_MIN bis RANGE_MAX
    buckets = range(RANGE_MIN, RANGE_MAX, STEP)

    counts: dict[str, dict[int, int]] = {
        fed_label: defaultdict(int) for fed_label in FEDERATIONS
    }

    for p in players:
        r = p["rating"]
        if r < RANGE_MIN or r >= RANGE_MAX:
            continue
        lo = (r // STEP) * STEP
        for fed_label, fed_code in FEDERATIONS.items():
            if fed_code is None or p["fed"] == fed_code:
                counts[fed_label][lo] += 1

    # Nur Ranges mit mindestens einem Spieler weltweit
    active_buckets = sorted(
        [lo for lo in buckets if counts["Welt"].get(lo, 0) > 0],
        reverse=True,
    )

    fed_labels = list(FEDERATIONS.keys())
    header = "| ELO-Range | " + " | ".join(fed_labels) + " |"
    sep    = "| ---       | " + " | ".join(["---:"] * len(fed_labels)) + " |"

    rows = [header, sep]
    for lo in active_buckets:
        marker = " ◀" if lo == ANCHOR else ""
        cells = [f"{counts[fl].get(lo, 0):,}" for fl in fed_labels]
        rows.append(f"| {elo_range_label(lo)}{marker} | " + " | ".join(cells) + " |")

    totals = [f"{sum(counts[fl].values()):,}" for fl in fed_labels]
    rows.append("| **Gesamt** | " + " | ".join(f"**{t}**" for t in totals) + " |")

    return "\n".join(rows)


def main():
    zip_path = DATA_DIR / "standard_dec25frl.zip"
    if not zip_path.exists():
        print(f"Datei nicht gefunden: {zip_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Lade {zip_path.name} ...")
    players = load_zip(zip_path)
    print(f"  {len(players):,} Spieler mit gültigem Rating geladen.")

    table = build_table(players)

    content = f"""# ELO-Verteilung Dezember 2025

Quelle: `standard_dec25frl.zip` (FIDE Standard-Rating-Liste, Dezember 2025)
Ranges in 100-Punkte-Schritten, Anker bei 2000 ELO (◀).
Nur Spieler mit aktivem Rating > 0.

{table}
"""
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Tabelle gespeichert: {OUTPUT}")
    print()
    print(table)


if __name__ == "__main__":
    main()
