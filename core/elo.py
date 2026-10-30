"""Python-Zwillinge der Elo-SQL-Funktionen.

  elo_band_floor/elo_band  ↔ fn_elo_band()/fn_elo_group()  (migrations/017)
  fide_expected/diff_cap   ↔ fn_fide_expected()/fn_fide_diff_cap() (migrations/018)

tests/test_elo_bands.py und tests/test_audit.py prüfen beide Seiten gegeneinander.
Ohne pandas-Abhängigkeit (Worker-Image), NaN wird trotzdem erkannt.
"""

import math
from datetime import date

# ── ELO-Bänder ───────────────────────────────────────────────────────────────
ELO_BAND_WIDTH = 50


def _missing(rating) -> bool:
    if rating is None:
        return True
    try:
        return math.isnan(rating)
    except TypeError:
        return False


def elo_band_floor(rating):
    """Untergrenze des 50er-Bands (2449 -> 2400). None bei fehlendem Rating."""
    if _missing(rating):
        return None
    return int(rating // ELO_BAND_WIDTH) * ELO_BAND_WIDTH


def elo_band(rating) -> str:
    """Bandbezeichner der Notebooks, z.B. '2400-2449', sonst 'unknown'.

    Format bewusst unverändert gegenüber der früheren Inline-Definition — die
    bereits committeten Notebook-Ausgaben (13/14) verwenden es in Achsen,
    Pivot-Indizes und Signifikanztabellen.
    """
    lo = elo_band_floor(rating)
    return "unknown" if lo is None else f"{lo}-{lo + ELO_BAND_WIDTH - 1}"


# ── FIDE-Erwartungswert ──────────────────────────────────────────────────────
# Obergrenzen |Δ| je Erwartungswert 0.50, 0.51, …, 0.99; darüber 1.00.
_FIDE_UPPER = (
    3, 10, 17, 25, 32, 39, 46, 53, 61, 68, 76, 83, 91, 98, 106, 113, 121, 129,
    137, 145, 153, 162, 170, 179, 188, 197, 206, 215, 225, 235, 245, 256, 267,
    278, 290, 302, 315, 328, 344, 357, 374, 391, 411, 432, 456, 484, 517, 559,
    619, 735,
)


def fide_expected(diff: int) -> float:
    """FIDE-Erwartungswert für diff = Ro − Gegner (ohne Kappung).

    Ganzzahlige Hundertstel statt 0.5 + i/100: letzteres liefert
    0.5700000000000001 und scheitert an jedem Gleichheitsvergleich.
    """
    a = abs(diff)
    hundredths = 100
    for i, upper in enumerate(_FIDE_UPPER):
        if a <= upper:
            hundredths = 50 + i
            break
    if diff < 0:
        hundredths = 100 - hundredths
    return hundredths / 100


def diff_cap(tournament_start: date) -> int | None:
    """400-Punkte-Regel nach Turnierbeginn: keine Kappung für Turniere mit
    Beginn 2022-01 bis 2024-02 (aus Daten abgeleitet, siehe Migration 018)."""
    if date(2022, 1, 1) <= tournament_start < date(2024, 3, 1):
        return None
    return 400
