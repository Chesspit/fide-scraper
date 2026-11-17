"""Migrationen der Reihe nach einspielen und protokollieren (Review F-18).

Bis Migration 030 wurden die Dateien von Hand per ``psql`` eingespielt; welche
davon auf der Produktion liefen, stand nirgends. Die Tabelle
``schema_migrations`` hält das fest: Dateiname, Prüfsumme, Zeitpunkt, Rolle.

Zwei Arten von Dateien:

* **normal** – die ganze Datei läuft in *einer* Transaktion zusammen mit dem
  Protokolleintrag. Scheitert ein Befehl, bleibt nichts zurück.
* **ohne Transaktion** – Kopfzeile ``-- migrate: no-transaction``. Für
  ``CREATE/DROP INDEX CONCURRENTLY`` und ``VACUUM``, die nicht in einem
  Transaktionsblock laufen dürfen. Jeder Befehl (durch ``;`` am Zeilenende
  getrennt) läuft einzeln; solche Dateien dürfen deshalb keine ``$$``-Körper
  enthalten und müssen mit ``IF [NOT] EXISTS`` wiederholbar sein, weil ein
  Abbruch mittendrin einen Teil der Befehle schon ausgeführt hat.

Ältere Dateien (015, 023–025, 027) enthalten ``CONCURRENTLY`` ohne Kopfzeile.
Deshalb gilt jede Datei mit ``CONCURRENTLY`` oder ``VACUUM`` ausserhalb von
Kommentaren ebenfalls als «ohne Transaktion».
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
NO_TRANSACTION_MARKER = "-- migrate: no-transaction"

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by TEXT NOT NULL DEFAULT current_user,
    baseline   BOOLEAN NOT NULL DEFAULT FALSE
)
"""


@dataclass(frozen=True)
class Migration:
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def number(self) -> int:
        return int(self.path.name.split("_", 1)[0])

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def no_transaction(self) -> bool:
        lines = self.sql.splitlines()
        if any(line.strip().lower() == NO_TRANSACTION_MARKER for line in lines):
            return True
        code = "\n".join(l for l in lines if not l.strip().startswith("--"))
        return re.search(r"\b(CONCURRENTLY|VACUUM)\b", code, re.IGNORECASE) is not None


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Alle ``NNN_*.sql`` aufsteigend nach Nummer."""
    files = [p for p in directory.glob("*.sql") if re.match(r"^\d{3}_", p.name)]
    migrations = sorted((Migration(p) for p in files), key=lambda m: (m.number, m.name))
    numbers = [m.number for m in migrations]
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes:
        raise ValueError(f"Migrationsnummer mehrfach vergeben: {dupes}")
    return migrations


def split_statements(sql: str) -> list[str]:
    """Befehle einer no-transaction-Datei: Kommentare raus, an ``;`` am Zeilenende trennen."""
    if "$$" in sql:
        raise ValueError("no-transaction-Migration darf keine $$-Körper enthalten")
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    parts = re.split(r";\s*$", "\n".join(lines), flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


def apply_sql(conn, migration: Migration) -> None:
    """Datei einspielen, ohne zu protokollieren (Tests, frische Datenbanken).

    Erwartet ``conn.autocommit = True``.
    """
    with conn.cursor() as cur:
        if migration.no_transaction:
            for stmt in split_statements(migration.sql):
                cur.execute(stmt)
        else:
            cur.execute(migration.sql)


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_TABLE_DDL)


def applied(conn) -> dict[str, str]:
    """Dateiname → Prüfsumme aller protokollierten Migrationen."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations')")
        if cur.fetchone()[0] is None:
            return {}
        cur.execute("SELECT filename, checksum FROM public.schema_migrations")
        return dict(cur.fetchall())


def _record(cur, migration: Migration, baseline: bool = False) -> None:
    cur.execute(
        "INSERT INTO public.schema_migrations (filename, checksum, baseline) "
        "VALUES (%s, %s, %s)",
        (migration.name, migration.checksum, baseline),
    )


def apply(conn, migration: Migration) -> None:
    """Eine Migration einspielen und protokollieren. Erwartet ``autocommit = True``."""
    if migration.no_transaction:
        apply_sql(conn, migration)
        with conn.cursor() as cur:
            _record(cur, migration)
        return
    with conn.cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute(migration.sql)
            _record(cur, migration)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise


def baseline(conn, upto: int, migrations: list[Migration] | None = None) -> list[Migration]:
    """Migrationen bis einschliesslich ``upto`` als eingespielt eintragen, ohne sie auszuführen.

    Für Datenbanken, auf denen die Dateien früher von Hand eingespielt wurden.
    Schon protokollierte Dateien bleiben unberührt.
    """
    migrations = list_migrations() if migrations is None else migrations
    ensure_table(conn)
    done = applied(conn)
    marked = [m for m in migrations if m.number <= upto and m.name not in done]
    with conn.cursor() as cur:
        cur.execute("BEGIN")
        for m in marked:
            _record(cur, m, baseline=True)
        cur.execute("COMMIT")
    return marked


def status(conn, migrations: list[Migration] | None = None) -> list[tuple[Migration, str]]:
    """Je Datei: ``applied``, ``pending`` oder ``changed`` (Prüfsumme weicht ab)."""
    migrations = list_migrations() if migrations is None else migrations
    done = applied(conn)
    out = []
    for m in migrations:
        if m.name not in done:
            out.append((m, "pending"))
        elif done[m.name] != m.checksum:
            out.append((m, "changed"))
        else:
            out.append((m, "applied"))
    return out


def pending(conn, migrations: list[Migration] | None = None) -> list[Migration]:
    return [m for m, s in status(conn, migrations) if s == "pending"]
