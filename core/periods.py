"""FIDE-Periodenkalender und die Regel „steht in der neuesten Liste".

Vorher verteilt auf scraper/db.py (is_valid_fide_period),
orchestrator/worker.py + sync_done_groups.py (valid_periods_for_year, zweimal),
orchestrator/audit.py (parse_period, previous_period) und
orchestrator/list_population.py (latest_list_period).
"""

from datetime import date


def is_valid_fide_period(d: date) -> bool:
    """Return True only for months FIDE actually published rating data.

    FIDE publishing history (verified against scrape_periods):
    - before 2008-04: no individual game data
    - 2008:           quarterly    → Apr, Jul, Oct
    - 2009:           5×/year      → Jan, Apr, Jul, Sep, Nov
    - 2010-01–2012-07: bi-monthly  → Jan, Mar, May, Jul, Sep, Nov
    - 2012-08+:        monthly     → all months
    """
    if d < date(2008, 4, 1):
        return False
    if d.year == 2008:
        return d.month in (4, 7, 10)
    if d.year == 2009:
        return d.month in (1, 4, 7, 9, 11)
    if d < date(2012, 8, 1):
        return d.month in (1, 3, 5, 7, 9, 11)
    return True


def last_complete_period(today: date | None = None) -> date:
    """Erster Tag des Vormonats — der laufende Monat hat noch keine FIDE-Daten."""
    today = today or date.today()
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)


def valid_periods_for_year(year: int, today: date | None = None) -> list[str]:
    """Gültige FIDE-Perioden des Jahres als ISO-Strings, gedeckelt auf den Vormonat."""
    cutoff = last_complete_period(today)
    periods = []
    for month in range(1, 13):
        d = date(year, month, 1)
        if d > cutoff:
            break
        if is_valid_fide_period(d):
            periods.append(d.isoformat())
    return periods


def parse_period(value: str) -> date:
    """'2020-01' oder '2020-01-01' → date(2020, 1, 1)."""
    parts = value.strip().split("-")
    if len(parts) < 2:
        raise ValueError(f"Periode erwartet als YYYY-MM: {value!r}")
    return date(int(parts[0]), int(parts[1]), 1)


def previous_period(p: date) -> date:
    """Vorherige gültige FIDE-Periode (Quartale/Zweimonatsrhythmus vor 2012-08)."""
    y, m = p.year, p.month
    for _ in range(24):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        if is_valid_fide_period(date(y, m, 1)):
            return date(y, m, 1)
    return date(y, m, 1)


# ── Neueste Liste ─────────────────────────────────────────────────────────────
# Regel: Wer in der neuesten importierten Liste nicht mehr mit Rating steht,
# gilt als von FIDE zurückgezogen (Beleg: ~14.000 März-2026-Erstratings,
# Profil "Not rated", Kalkulation "No records found"). Der Scraper holt solche
# Kombos nicht (orchestrator/list_population.py), das Audit führt sie als INFO
# (qc/audit.py). Beide nutzen dieselbe Bedingung von hier.

def latest_list_period(cur) -> date | None:
    """Neueste importierte Liste (Index-Scan auf idx_rating_history_period_pubrating)."""
    cur.execute("SELECT max(period) FROM rating_history WHERE published_rating > 0")
    return cur.fetchone()[0]


def in_latest_list_sql(alias: str, latest_param: str = "%s") -> str:
    """SQL-Bedingung: Spieler {alias}.fide_id steht in der neuesten Liste mit Rating.

    latest_param ist der Platzhalter für die Periode, z. B. "%s" oder "%(latest)s".
    """
    return (f"EXISTS (SELECT 1 FROM rating_history lt "
            f"WHERE lt.fide_id = {alias}.fide_id AND lt.period = {latest_param} "
            f"AND lt.published_rating > 0)")
