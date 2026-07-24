"""ARPAD — Chat-Assistent für Fragen zu den FIDE-Rating-Daten.

Anthropic-Client, Tool-Definitionen (über @beta_tool-Wrapper um data_chat_queries.py-
Funktionen), System-Prompt, und der öffentliche Einstiegspunkt answer_question().
"""
import json
from pathlib import Path

import anthropic
from anthropic import beta_tool
from dotenv import load_dotenv

import data_chat_queries as q

# frontend/data_arpad.py -> .parent = frontend/ -> .parent = Repo-Root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # liest ANTHROPIC_API_KEY aus der Umgebung
    return _client


class ArpadError(Exception):
    """Nutzerfreundlicher Fehler — die Page zeigt str(e) direkt als Chat-Bubble an."""


SYSTEM_PROMPT = """\
Du bist ARPAD, ein Chat-Assistent für die FIDE-Rating-Datenbank dieses Projekts.

Aufgabe: Beantworte Fragen zu den in der Datenbank vorhandenen FIDE-Rating- und Partiedaten
der gescrapten Spieler mithilfe der dir zur Verfügung stehenden Tools.

Wichtige Regeln:
- Rufe IMMER zuerst `search_player` auf, um die fide_id eines genannten Spielers zu ermitteln
  — außer der Nutzer nennt bereits eine numerische FIDE-ID direkt.
- Wenn `search_player` mehrere plausible Treffer liefert, frage nach, welchen Spieler der
  Nutzer meint, statt zu raten.
- Wenn ein Tool meldet, dass keine Partie-/QC-Daten für einen Spieler vorliegen, sag das klar
  und ehrlich — dieses Projekt hat nur für einen Kern-Datensatz von ca. 14.000+ Spielern
  (Top-ELO-/Analysegruppen/Swiss-2026) tatsächliche Partiedaten gescraped, nicht für alle
  ca. 1,8 Mio. FIDE-Spieler.
- Du hast KEINEN Zugriff auf den Scraping-Prozess selbst (Status, Fortschritt, Konfiguration,
  wann welche Gruppe gescraped wurde). Bei solchen Fragen weise freundlich darauf hin, dass
  das außerhalb deines Aufgabenbereichs liegt — du beantwortest nur Fragen über bereits
  vorhandene Daten.
- Antworte prägnant und auf Deutsch, in normalem Fließtext. Nutze Zahlen aus den Tool-
  Ergebnissen präzise; erfinde keine Werte.
- Wenn eine Frage nichts mit den FIDE-Rating-/Partiedaten zu tun hat, lehne freundlich ab und
  erkläre kurz deinen Aufgabenbereich.
"""


def _to_json(obj) -> str:
    """Tool-Ergebnisse müssen laut Tool-Runner str oder Content-Block-Liste sein — nie dict."""
    return json.dumps(obj, default=str, ensure_ascii=False)


@beta_tool
def search_player(name_query: str, limit: int = 10) -> str:
    """Sucht Spieler nach Name (Teilstring) oder FIDE-ID.

    Rufe dies IMMER zuerst auf, wenn der Nutzer einen Spieler beim Namen erwähnt — bevor du
    andere Tools mit einer fide_id aufrufst. Gibt bis zu `limit` Kandidaten mit fide_id, Name,
    Föderation, Titel und Rating zurück, sowie ob für den Spieler Partiedaten vorliegen
    (has_game_data).

    Args:
        name_query: Name (mind. 2 Zeichen, Teilstring-Suche) oder FIDE-ID als Zahl/String.
        limit: maximale Anzahl Treffer (Standard 10).
    """
    try:
        return _to_json({"results": q.search_player(name_query, limit=limit)})
    except Exception as e:
        return _to_json({"error": f"Datenbankfehler bei der Spielersuche: {e}"})


@beta_tool
def get_player_rating_history(fide_id: int, year_from: int = 0, year_to: int = 0) -> str:
    """Liefert die monatliche Rating-Historie eines Spielers.

    Rufe dies auf, wenn nach der Entwicklung/dem Verlauf des Ratings eines Spielers über die
    Zeit gefragt wird. Erfordert eine bekannte fide_id (zuerst search_player aufrufen, falls
    nur ein Name bekannt ist).

    Args:
        fide_id: FIDE-ID des Spielers.
        year_from: optionales Startjahr (0 = kein Filter).
        year_to: optionales Endjahr (0 = kein Filter).
    """
    try:
        return _to_json(q.get_player_rating_history(
            fide_id, year_from=year_from or None, year_to=year_to or None))
    except Exception as e:
        return _to_json({"error": f"Datenbankfehler bei der Rating-Historie: {e}"})


@beta_tool
def get_player_game_stats(fide_id: int, opponent_sex: str = "", year_from: int = 0,
                           year_to: int = 0) -> str:
    """Liefert aggregierte Partie-Statistiken eines Spielers (Anzahl, Score-Quote,
    Gegner-Rating-Schnitt, Farbverteilung).

    Rufe dies auf bei Fragen zu Partien-Anzahl, Erfolgsquote, Gegnerstärke oder ähnlichen
    aggregierten Kennzahlen. Meldet explizit, wenn keine Partiedaten für den Spieler vorliegen
    (Spieler außerhalb des gescrapten Kern-Datensatzes).

    Args:
        fide_id: FIDE-ID des Spielers.
        opponent_sex: "M", "F" oder "" für kein Filter nach Gegner-Geschlecht.
        year_from: optionales Startjahr (0 = kein Filter).
        year_to: optionales Endjahr (0 = kein Filter).
    """
    try:
        return _to_json(q.get_player_game_stats(
            fide_id,
            opponent_sex=opponent_sex or None,
            year_from=year_from or None,
            year_to=year_to or None,
        ))
    except Exception as e:
        return _to_json({"error": f"Datenbankfehler bei den Partie-Statistiken: {e}"})


@beta_tool
def get_player_qc_summary(fide_id: int) -> str:
    """Liefert eine Zusammenfassung der Datenqualitäts-Prüfungen (QC) für einen Spieler —
    wie viele Rating-Fenster als ok/warn/error markiert wurden.

    Rufe dies auf, wenn nach der Zuverlässigkeit/Plausibilität der Rating-Daten eines Spielers
    gefragt wird (z.B. "wie verlässlich ist das Rating von X").

    Args:
        fide_id: FIDE-ID des Spielers.
    """
    try:
        return _to_json(q.get_player_qc_summary(fide_id))
    except Exception as e:
        return _to_json({"error": f"Datenbankfehler bei der QC-Zusammenfassung: {e}"})


TOOLS = [search_player, get_player_rating_history, get_player_game_stats, get_player_qc_summary]


def answer_question(question: str, history: list[dict]) -> tuple[str, list[dict]]:
    """Beantwortet eine Nutzerfrage; rundtrippt die Conversation-History.

    Args:
        question: neue Nutzerfrage (roher Text, bereits .strip()-t vom Aufrufer).
        history: bisherige Anthropic-messages-Liste [{"role": ..., "content": "..."}], wie sie
            im dcc.Store gehalten wird — OHNE die neue Frage.

    Returns:
        (answer_text, new_history) — new_history = history + user-turn + assistant-turn,
        direkt geeignet zum Zurückschreiben in den dcc.Store.

    Raises:
        ArpadError: bei Anthropic-API-Fehlern (Auth/Rate-Limit/Verbindung/Server) — die Page
        fängt das ab und zeigt es als Fehler-Bubble.
    """
    messages = list(history) + [{"role": "user", "content": question}]
    client = _get_client()

    try:
        runner = client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        final_message = None
        for message in runner:
            final_message = message
    except anthropic.AuthenticationError as e:
        raise ArpadError(
            "Anthropic API-Schlüssel fehlt oder ist ungültig — ANTHROPIC_API_KEY in .env prüfen."
        ) from e
    except anthropic.RateLimitError as e:
        raise ArpadError(
            "Zu viele Anfragen an Claude gerade — bitte kurz warten und erneut versuchen."
        ) from e
    except anthropic.APIConnectionError as e:
        raise ArpadError("Verbindung zu Claude fehlgeschlagen — Netzwerkproblem?") from e
    except anthropic.NotFoundError as e:
        raise ArpadError("Anthropic-Modell nicht gefunden (Konfigurationsfehler in ARPAD).") from e
    except anthropic.APIStatusError as e:
        raise ArpadError(
            f"Claude-Dienst-Fehler ({e.status_code}) — bitte später erneut versuchen."
        ) from e

    if final_message is None:
        raise ArpadError("Claude hat keine Antwort geliefert — bitte erneut versuchen.")

    answer_text = next(
        (b.text for b in final_message.content if b.type == "text"), ""
    ).strip() or "Ich konnte keine Antwort formulieren — bitte die Frage anders stellen."

    new_history = messages + [{"role": "assistant", "content": answer_text}]
    return answer_text, new_history
