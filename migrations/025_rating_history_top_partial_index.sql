-- owner: frontend
-- Migration 025: Teilindex für Spieler mit Elo >= 2700
--
-- frontend/data.py::load_2700_history() sucht alle fide_id mit
-- published_rating >= 2700 seit 2009. Ohne passenden Index war das ein
-- Parallel Seq Scan über rating_history (61 Mio. Zeilen, 2,6 GB): 70–170 s,
-- je nach Last — die Startseite /c hing so lange. Der Teilindex enthält nur
-- die wenigen tausend 2700er-Zeilen. CONCURRENTLY: siehe 023.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rating_history_pub2700
    ON rating_history (period, fide_id)
    WHERE published_rating >= 2700;
