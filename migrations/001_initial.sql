-- =============================================================================
-- FIDE Scraper — Initial Schema
-- Migration: 001_initial.sql
-- =============================================================================
-- Änderungshistorie:
--   2026-04-14  Initiale Version
--   2026-04-14  k_factor in scrape_periods ergänzt (relevant für Rating-Volatilitäts-
--               analyse: erklärt warum gleiche Ergebnisse unterschiedliche Rating-
--               Änderungen erzeugen)
--               opponent_federation in game_results ergänzt
--               UNIQUE-Constraint in game_results robuster gestaltet:
--               opponent_rating statt result als Diskriminator, um Duplikate bei
--               Remis-Serien gegen denselben Gegner zu vermeiden
-- =============================================================================

-- ---------------------------------------------------------------------------
-- players — bekannte Spieler-IDs inkl. Analyse-Gruppe
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    fide_id         INTEGER PRIMARY KEY,
    name            TEXT,
    federation      TEXT,
    title           TEXT,
    sex             CHAR(1),                    -- 'M' | 'F'
    birth_year      INTEGER,
    std_rating      INTEGER,
    analysis_group  TEXT,                       -- 'female_top' | 'male_control'
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- scrape_periods — welche (player, period)-Kombinationen bereits gescraped wurden
--
-- k_factor: Der FIDE-K-Faktor des Spielers in dieser Periode.
--   Werte: 40 (Neueinsteiger <30 Partien), 20 (Standard), 10 (>2400 ELO).
--   Wird auf der Calculations-Seite pro Periode ausgewiesen.
--   Relevant für die Analyse der Rating-Volatilität: Ein K=40-Spieler
--   erzielt bei identischen Ergebnissen doppelt so große Rating-Änderungen
--   wie ein K=20-Spieler — ohne dieses Feld wäre der Volatilitätsvergleich
--   zwischen Spielern unterschiedlicher Erfahrung verzerrt.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_periods (
    fide_id     INTEGER NOT NULL REFERENCES players(fide_id),
    period      DATE NOT NULL,                  -- immer 1. des Monats, z.B. 2025-10-01
    scraped_at  TIMESTAMPTZ DEFAULT NOW(),
    status      TEXT DEFAULT 'ok',              -- 'ok' | 'no_data' | 'error'
    k_factor    INTEGER,                        -- 10 | 20 | 40; NULL wenn nicht parsebar
    PRIMARY KEY (fide_id, period)
);

-- ---------------------------------------------------------------------------
-- game_results — Einzelpartien aus den Calculations-Seiten
--
-- opponent_federation: FIDE-Föderationscode des Gegners (3-stellig, z.B. 'GER').
--   Wird in der Calculations-Tabelle neben dem Gegnernamen angezeigt.
--   Optional für die Kernanalyse, aber nützlich für geografische Auswertungen
--   (z.B. spielen Frauen häufiger gegen Gegner aus bestimmten Föderationen?).
--
-- UNIQUE-Constraint (fide_id, period, opponent_name, opponent_rating, result):
--   Diskriminiert über opponent_rating statt nur über result, um Kollisionen
--   bei Remis-Serien gegen denselben Gegner innerhalb einer Periode zu vermeiden.
--   Beispiel: Spielerin A trifft Spieler B (Rtg 2450) zweimal im selben Monat
--   und macht 0.5 — ohne opponent_rating würde der zweite Insert als Duplikat
--   abgewiesen. Bekannte Schwäche: zwei Partien gegen denselben Gegner mit
--   identischem Rating und identischem Ergebnis werden noch immer dedupliziert.
--   In der Praxis tritt das sehr selten auf; für exakte Deduplication wäre
--   eine game_id aus FIDE nötig (nicht öffentlich verfügbar).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_results (
    id                  BIGSERIAL PRIMARY KEY,
    fide_id             INTEGER NOT NULL REFERENCES players(fide_id),
    period              DATE NOT NULL,
    opponent_name       TEXT,
    opponent_fide_id    INTEGER,
    opponent_federation CHAR(3),                -- z.B. 'GER', 'CHN'; NULL wenn nicht parsebar
    opponent_rating     INTEGER,
    result              TEXT,                   -- '1' | '0.5' | '0'
    rating_change       NUMERIC(5,1),
    tournament_name     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fide_id, period, opponent_name, opponent_rating, result)
);

CREATE INDEX ON game_results (fide_id, period);
CREATE INDEX ON game_results (period);

-- ---------------------------------------------------------------------------
-- rating_history — monatliches Rating pro Spieler (aus FIDE-Download-Liste)
--
-- std_rating ist das publizierte Start-Rating der Periode (vor den Partien).
-- Das End-Rating einer Periode ergibt sich aus:
--   std_rating + SUM(game_results.rating_change) für diese (fide_id, period).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rating_history (
    fide_id     INTEGER NOT NULL REFERENCES players(fide_id),
    period      DATE NOT NULL,
    std_rating  INTEGER,                        -- publiziertes Rating zu Periodenbeginn
    num_games   INTEGER,
    PRIMARY KEY (fide_id, period)
);
