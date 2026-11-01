"""Verbindung der Qualitätskontrolle (Rolle qc, Phase 2a).

search_path enthält orchestrator, weil integrity.py die Queue (scrape_groups)
liest. Kein Schema-DDL, kein Retry — anders als orchestrator.setup_db.connect.
"""

from core.db import connect as _connect

SEARCH_PATH = "orchestrator,public"


def connect(dsn: str | None = None):
    """Autocommit-Verbindung für QC-Abfragen und qc_*-Ergebnistabellen."""
    return _connect(dsn, role="qc", search_path=SEARCH_PATH, connect_timeout=10,
                    autocommit=True)
