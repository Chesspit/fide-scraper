-- owner: ingest
-- Migration 027: Teilindex für noch nicht angereicherte Partien
--
-- scripts/resolve_opponents.py --pending sucht täglich (monthly_update.sh) die
-- Perioden mit Partien ohne opponent_match_quality. Ohne Index wäre das ein
-- Seq Scan über game_results (~20 Mio. Zeilen, 8 GB). Nach dem Gesamtlauf
-- (11.11.2026) sind nur neu gescrapte Partien betroffen, der Index bleibt klein.
-- CONCURRENTLY: siehe 023.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_game_results_pending_enrich
    ON game_results (period)
    WHERE opponent_match_quality IS NULL;
