"""Konfiguration und DB-URL — die eine Stelle für alle Blöcke.

Vorher gab es vier Wege zur DATABASE_URL (scraper.config, os.environ im
Frontend, .env.notebook-Loader, eigenes load_dotenv in data_arpad.py).

Auflösung:
  1. Umgebungsvariable (im Container gesetzt; load_dotenv überschreibt nie)
  2. .env im Repo-Root (lokal)

Rollen (Phase 2a, docs/db_rollen.md): get_database_url("reader") liest
DATABASE_URL_READER und fällt auf DATABASE_URL zurück. So können Dienste
einzeln auf die neue Rolle umgestellt werden, ohne Codeänderung.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

ROLES = ("ingest", "qc", "reader")


def get_database_url(role: str | None = None) -> str:
    """DSN für die gegebene Rolle, sonst die allgemeine DATABASE_URL."""
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"Unbekannte DB-Rolle {role!r}, erlaubt: {ROLES}")
        url = os.environ.get(f"DATABASE_URL_{role.upper()}")
        if url:
            return url
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set. Check .env or environment variables.")
    return url


def get_notebook_database_url() -> str:
    """DSN für Notebooks/marimo: .env.notebook (Tunnel localhost:5434) vor allem anderen.

    Die Notebook-Datei hat Vorrang, weil .env lokal auch Werte für andere
    Ziele tragen kann; fehlt sie, gilt die Leserolle wie überall.
    """
    url = dotenv_values(PROJECT_ROOT / ".env.notebook").get("DATABASE_URL")
    return url or get_database_url("reader")
