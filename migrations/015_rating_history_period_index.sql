-- Migration 015: Index auf rating_history.period (nur Zeilen mit published_rating)
--
-- rating_history hatte bisher nur den zusammengesetzten PK (fide_id, period) —
-- jede Abfrage, die ausschließlich nach period filtert (z.B. "wie viele Spieler
-- hat die aktuelle FIDE-Standardliste", orchestrator/store.py::query_pg_players()),
-- erzwang einen Full-Table-Scan (gemessen: 12–75 s pro Periode, 2026-09-01).
-- Partial Index (nur published_rating IS NOT NULL) hält ihn klein und deckt
-- genau die TXT-Snapshot-Zeilen ab, um die es bei periodenbasierten Queries geht.
--
-- CONCURRENTLY: läuft außerhalb einer Transaktion, blockiert keine laufenden
-- Reads/Writes auf der produktiven, mit tunnelbliq geteilten DB. Dieses
-- Statement daher NICHT in ein BEGIN/COMMIT wrappen.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rating_history_period_pubrating
    ON rating_history (period)
    WHERE published_rating IS NOT NULL;
