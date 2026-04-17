import logging
import re
from datetime import date

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _safe_int(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d\-]", "", text.strip())
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return None


def _safe_float(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d\.\-]", "", text.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _safe_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _normalize_result(raw: str | None) -> str | None:
    if not raw:
        return None
    val = raw.strip()
    if val in ("1.00", "1", "1.0"):
        return "1"
    if val in ("0.50", "0.5", "=", "\u00bd"):
        return "0.5"
    if val in ("0.00", "0", "0.0"):
        return "0"
    return val


def _extract_color(td) -> str | None:
    """Extract player color from the CSS class of the span in the name cell."""
    span = td.find("span")
    if not span:
        return None
    classes = span.get("class", [])
    if "white_note" in classes:
        return "W"
    if "black_note" in classes:
        return "B"
    return None


def _extract_opponent_name(td) -> str | None:
    """Extract opponent name from the first cell, stripping the color span."""
    text = td.get_text(strip=True)
    if not text:
        return None
    return text.strip()


def _clean_rating_text(td) -> str:
    """Remove <font> tags and non-numeric decoration from a rating cell."""
    for font_tag in td.find_all("font"):
        font_tag.decompose()
    return td.get_text(strip=True)


def _parse_tournament_header(div_elements, table_index, all_divs):
    """Find the tournament header divs preceding a calc_table.

    Returns (name, location, start_date, end_date) or (None, None, None, None).
    """
    name = None
    location = None
    start_date = None
    end_date = None

    # Walk backwards from current position to find rtng_line01 / rtng_line02
    for div in div_elements:
        classes = div.get("class", [])
        if "rtng_line01" in classes:
            link = div.find("a")
            if link:
                name = link.get_text(strip=True)
            elif div.find("strong"):
                pass  # period header, not tournament
        elif "rtng_line02" in classes:
            strong = div.find("strong")
            if strong:
                location = strong.get_text(strip=True)
            date_spans = div.find_all("span", class_="dates_span")
            if len(date_spans) >= 1:
                start_date = _safe_date(date_spans[0].get_text(strip=True))
            if len(date_spans) >= 2:
                end_date = _safe_date(date_spans[1].get_text(strip=True))

    return name, location, start_date, end_date


def parse_calculations(
    html: str, fide_id: int, period_str: str
) -> tuple[list[dict], int | None, int | None]:
    """Parse a FIDE calculations AJAX HTML fragment.

    Args:
        html: The HTML fragment from the AJAX response
        fide_id: The FIDE player ID
        period_str: Period string "YYYY-MM-01"

    Returns:
        Tuple of (games, k_factor, own_rating):
        - games: list of dicts with game data
        - k_factor: K-factor (10/20/40) or None
        - own_rating: player's own rating (Ro) or None
    """
    if not html or not html.strip():
        return [], None, None

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", class_="calc_table")

    if not tables:
        return [], None, None

    games = []
    k_factor = None
    own_rating = None
    game_index = 0

    for table in tables:
        # Find tournament header: divs before this table
        tournament_name = None
        tournament_location = None
        tournament_start_date = None
        tournament_end_date = None

        # Walk previous siblings to find tournament header divs
        header_divs = []
        sibling = table.previous_sibling
        while sibling:
            if hasattr(sibling, "name") and sibling.name == "div":
                classes = sibling.get("class", [])
                if "default_div_full" in classes:
                    header_divs.insert(0, sibling)
                else:
                    break
            elif hasattr(sibling, "name") and sibling.name == "table":
                break  # hit previous table
            sibling = sibling.previous_sibling

        for div in header_divs:
            inner_divs = div.find_all("div", recursive=False)
            if not inner_divs:
                inner_divs = [div]
            for inner in inner_divs:
                classes = inner.get("class", [])
                if "rtng_line01" in classes:
                    link = inner.find("a")
                    if link:
                        tournament_name = link.get_text(strip=True)
                elif "rtng_line02" in classes:
                    strong = inner.find("strong")
                    if strong:
                        tournament_location = strong.get_text(strip=True)
                    date_spans = inner.find_all("span", class_="dates_span")
                    if len(date_spans) >= 1:
                        tournament_start_date = _safe_date(
                            date_spans[0].get_text(strip=True)
                        )
                    if len(date_spans) >= 2:
                        tournament_end_date = _safe_date(
                            date_spans[1].get_text(strip=True)
                        )

        rows = table.find_all("tr")

        # Extract Ro (own rating) from summary row (bgcolor=#e6e6e6)
        for row in rows:
            if row.get("bgcolor") == "#e6e6e6":
                cells = row.find_all("td")
                if len(cells) >= 2:
                    ro = _safe_int(cells[1].get_text(strip=True))
                    if ro and own_rating is None:
                        own_rating = ro
                break

        # Parse game rows (bgcolor=#efefef with class=list4 cells)
        for row in rows:
            if row.get("bgcolor") != "#efefef":
                continue

            cells = row.find_all("td")
            if len(cells) < 10:
                continue

            # Check this is a game row (has list4 class) not a spacer
            if not any("list4" in (c.get("class") or []) for c in cells):
                continue

            color = _extract_color(cells[0])
            opponent_name = _extract_opponent_name(cells[0])
            opponent_title = cells[1].get_text(strip=True) or None
            opponent_women_title = cells[2].get_text(strip=True) or None
            opponent_rating = _safe_int(_clean_rating_text(cells[3]))
            opponent_federation = cells[4].get_text(strip=True) or None
            result = _normalize_result(cells[5].get_text(strip=True))
            rating_change = _safe_float(cells[7].get_text(strip=True))
            k = _safe_int(cells[8].get_text(strip=True))
            rating_change_weighted = _safe_float(cells[9].get_text(strip=True))

            if k and k_factor is None:
                k_factor = k

            # Normalize federation to 3 chars or None
            if opponent_federation and len(opponent_federation) != 3:
                opponent_federation = None

            games.append(
                {
                    "fide_id": fide_id,
                    "period": period_str,
                    "opponent_name": opponent_name,
                    "opponent_title": opponent_title,
                    "opponent_women_title": opponent_women_title,
                    "opponent_rating": opponent_rating,
                    "opponent_federation": opponent_federation,
                    "result": result,
                    "rating_change": rating_change,
                    "rating_change_weighted": rating_change_weighted,
                    "color": color,
                    "tournament_name": tournament_name,
                    "tournament_location": tournament_location,
                    "tournament_start_date": tournament_start_date,
                    "tournament_end_date": tournament_end_date,
                    "game_index": game_index,
                }
            )
            game_index += 1

    return games, k_factor, own_rating
