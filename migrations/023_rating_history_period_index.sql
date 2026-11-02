-- owner: qc
-- Migration 023: Index auf rating_history.period (alle Zeilen)
--
-- Die Datenprüfung (qc/audit.py) fragt je Periode alle Spieler ab. Der Partial Index
-- aus 015 (published_rating IS NOT NULL) greift bei num_games > 0 nicht, es blieb ein
-- Full-Table-Scan über 60 Mio. Zeilen je Periode, mehrfach pro Lauf. Der Lauf ab 2020
-- brach am 02.11.2026 am 15-Minuten-Limit ab.
--
-- Btree-Deduplizierung hält den Index klein, weil period nur wenige Werte hat.
--
-- CONCURRENTLY: blockiert keine laufenden Reads/Writes, darf aber nicht in einer
-- Transaktion laufen. Deshalb nicht in BEGIN/COMMIT wrappen und nur ein Statement
-- je Datei (mehrere in einem execute() wären eine implizite Transaktion).

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rating_history_period
    ON rating_history (period);
