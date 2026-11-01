"""FIDE-Spielerliste (TXT/ZIP, Festbreitenformat) lesen.

Geteilt von Block 1 (orchestrator/generate_groups.py,
scripts/import_rating_snapshots.py) und dem eingefrorenen System A
(scripts/legacy/seed_players.py). Vorher in scripts/seed_players.py — dadurch
hing System B an System A, und das Orchestrator-Image musste ganz scripts/
kopieren (Phase 4).
"""

import logging
import re
import zipfile
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# Historical FIDE lists use month-based rating column labels (e.g. "FEB15", "JAN26").
# Current lists use "SRtng". The detector below tries "SRtng" first, then the pattern.
MONTH_RATING_PATTERN = re.compile(
    r"\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}\b",
    re.IGNORECASE  # pre-2015 FIDE files use lowercase (e.g. "sep12")
)

# Column positions in FIDE fixed-width TXT file (0-indexed, verified April 2026)
DEFAULT_COLUMNS = {
    "id": (0, 15),
    "name": (15, 76),
    "federation": (76, 79),
    "sex": (80, 81),
    "title": (84, 87),
    "women_title": (89, 92),
    "std_rating": (113, 118),
    "birth_year": (152, 156),
    "flag": (158, 162),
}


def detect_columns_from_header(header_line: str) -> dict:
    """Try to detect column positions from the header line.

    Handles three formats:
    - Current (SRtng rating col, Sex/WTit present)
    - Historical 2012-2015 (month-based rating col e.g. FEB15, Sex/WTit present)
    - Pre-2013 legacy (month-based e.g. sep09, NO Sex/WTit, shorter layout)
    Falls back to DEFAULT_COLUMNS if detection fails.
    """
    # ── Pre-2013 legacy format: "ID number" (lowercase n), no Sex/WTit ──────
    # Header example: "ID number Name                              TitlFed  Sep09 GamesBorn  Flag"
    if "ID number" in header_line:
        cols = {
            "id":          (0,  9),
            "name":        (10, 44),
            "title":       (44, 47),
            "federation":  (48, 51),
            "birth_year":  (64, 68),
            "flag":        (70, 74),
        }
        m = MONTH_RATING_PATTERN.search(header_line)
        if m:
            cols["std_rating"] = (m.start(), m.start() + 5)
            # "GamesBorn" in pre-2013: Games immediately follows the rating (5 chars wide)
            games_pos = header_line.find("Games")
            if games_pos >= 0:
                cols["std_games"] = (games_pos, games_pos + 5)
            logger.info("Detected pre-2013 column layout (rating col: %s, no Sex/WTit): %s",
                        m.group(), cols)
            return cols
        logger.warning("Pre-2013 header found but no rating column detected")
        return DEFAULT_COLUMNS

    # ── Current and 2012-2015 formats ────────────────────────────────────────
    cols = {}
    markers = {
        "id": "ID Number",
        "name": "Name",
        "federation": "Fed",
        "sex": "Sex",
        "title": "Tit",
        "women_title": "WTit",
        "birth_year": "B-day",
        "flag": "Flag",
    }
    widths = {
        "id": 15, "name": 61, "federation": 3, "sex": 1,
        "title": 3, "women_title": 3, "birth_year": 4, "flag": 4,
    }

    for key, marker in markers.items():
        pos = header_line.find(marker)
        if pos >= 0:
            cols[key] = (pos, pos + widths[key])

    # Rating column: prefer "SRtng" (current), fall back to month-based label
    rating_pos = header_line.find("SRtng")
    rating_marker = "SRtng"
    if rating_pos < 0:
        m = MONTH_RATING_PATTERN.search(header_line)
        if m:
            rating_pos = m.start()
            rating_marker = m.group()
    if rating_pos >= 0:
        cols["std_rating"] = (rating_pos, rating_pos + 5)

    # Standard Games played this period — column name changed over the years:
    # Pre-2013: "Games", 2013-2025: "Gms", 2026+: "SGm"
    for games_label, width in [("SGm", 4), ("Gms", 4), ("Games", 5)]:
        gpos = header_line.find(games_label)
        if gpos >= 0:
            cols["std_games"] = (gpos, gpos + width)
            break

    if len(cols) >= 6:
        logger.info("Detected column positions (rating col: %s): %s", rating_marker, cols)
        return cols

    logger.warning("Could not detect all columns from header, using defaults")
    return DEFAULT_COLUMNS


def open_player_list(filepath: Path) -> Iterator[str]:
    """Yield text lines from a FIDE player list (TXT or ZIP containing a TXT)."""
    if filepath.suffix.lower() == ".zip":
        with zipfile.ZipFile(filepath) as zf:
            inner_names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not inner_names:
                raise ValueError(f"No .txt file inside {filepath}")
            with zf.open(inner_names[0]) as fp:
                for raw in fp:
                    yield raw.decode("latin-1", errors="replace")
    else:
        with open(filepath, encoding="latin-1") as f:
            yield from f


def parse_player_line(line: str, columns: dict) -> dict | None:
    """Parse a single line from the FIDE TXT file into a player dict.

    Pre-2013 files have shorter lines and no Sex/WTit columns — those
    fields default to None and are handled gracefully.
    """
    # Pre-2013 lines can be as short as ~60 chars; newer format needs ~100.
    min_len = 55 if "sex" not in columns else 100
    if len(line) < min_len:
        return None

    def extract(key):
        start, end = columns[key]
        return line[start:end].strip() if len(line) > start else ""

    fide_id_str = extract("id")
    if not fide_id_str or not fide_id_str.isdigit():
        return None

    name = extract("name")
    if not name:
        return None

    rating_str = extract("std_rating") if "std_rating" in columns else ""
    try:
        std_rating = int(rating_str) if rating_str else 0
    except ValueError:
        std_rating = 0

    birth_str = extract("birth_year") if "birth_year" in columns else ""
    try:
        birth_year = int(birth_str) if birth_str and len(birth_str) == 4 else None
    except ValueError:
        birth_year = None

    title = extract("title") if "title" in columns else None
    title = title or None

    women_title = extract("women_title") if "women_title" in columns else None
    women_title = women_title or None

    flag = extract("flag").lower() if "flag" in columns else ""
    # FIDE flags: 'i' = inactive, 'wi' = woman inactive. Anything else → active.
    active = flag not in ("i", "wi")

    games_str = extract("std_games") if "std_games" in columns else ""
    try:
        std_games = int(games_str) if games_str else None
    except ValueError:
        std_games = None

    return {
        "fide_id": int(fide_id_str),
        "name": name,
        "federation": extract("federation") if "federation" in columns else None,
        "sex": extract("sex") if "sex" in columns else None,
        "title": title,
        "women_title": women_title,
        "std_rating": std_rating,
        "birth_year": birth_year,
        "active": active,
        "std_games": std_games,
    }


def load_players_from_file(filepath: Path) -> list[dict]:
    """Parse a FIDE TXT or ZIP file and return all player dicts."""
    logger.info("Reading %s ...", filepath)

    lines = open_player_list(filepath)
    header = next(lines)
    columns = detect_columns_from_header(header)

    players = []
    for line in lines:
        p = parse_player_line(line.rstrip("\n\r"), columns)
        if p:
            players.append(p)

    logger.info("Parsed %d players from file", len(players))
    return players
