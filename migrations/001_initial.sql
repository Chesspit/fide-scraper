-- =============================================================================
-- FIDE Scraper — Initial Schema
-- Migration: 001_initial.sql
-- =============================================================================
-- Änderungshistorie:
--   2026-04-14  Initiale Version
--   2026-04-17  Schema-Überarbeitung nach HTML-Analyse:
--               - players: women_title ergänzt, federation CHAR(3), Indizes für Lookup
--               - game_results: game_index als UNIQUE-Basis, color, opponent_title,
--                 opponent_women_title, rating_change_weighted, Turnier-Details
--               - rating_history: published_rating für Validierung gegen TXT-Snapshots
-- =============================================================================

-- ---------------------------------------------------------------------------
-- players — alle Spieler aus der FIDE-Download-Liste (Lookup + Analyse)
--
-- Enthält ~1,8 Mio Spieler. analysis_group ist nur für die 194 Analyse-Spieler
-- gesetzt, alle übrigen dienen als Lookup für die Gegner-Auflösung.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    fide_id         INTEGER PRIMARY KEY,
    name            TEXT,
    federation      CHAR(3),
    title           TEXT,                           -- GM, IM, FM, CM oder NULL
    women_title     TEXT,                           -- WGM, WIM, WFM oder NULL
    sex             CHAR(1),                        -- 'M' | 'F'
    birth_year      INTEGER,
    std_rating      INTEGER,                        -- letztes bekanntes Standard-Rating
    analysis_group  TEXT,                           -- 'female_top' | 'male_control' | NULL
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_players_name_fed ON players (name, federation);
CREATE INDEX IF NOT EXISTS idx_players_analysis_group ON players (analysis_group)
    WHERE analysis_group IS NOT NULL;

-- ---------------------------------------------------------------------------
-- scrape_periods — welche (player, period)-Kombinationen bereits gescraped wurden
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_periods (
    fide_id     INTEGER NOT NULL REFERENCES players(fide_id),
    period      DATE NOT NULL,                      -- immer 1. des Monats, z.B. 2025-10-01
    scraped_at  TIMESTAMPTZ DEFAULT NOW(),
    status      TEXT DEFAULT 'ok',                  -- 'ok' | 'no_data' | 'error'
    k_factor    INTEGER,                            -- 10 | 20 | 40; NULL wenn nicht parsebar
    PRIMARY KEY (fide_id, period)
);

-- ---------------------------------------------------------------------------
-- game_results — Einzelpartien aus den Calculations-Seiten
--
-- game_index: Laufende Nummer der Partie innerhalb (fide_id, period).
--   Löst das Duplikat-Problem bei identischen Gegner/Rating/Ergebnis-Kombinationen.
--
-- color: Spielerfarbe, abgeleitet aus CSS-Klasse (white_note/black_note).
--
-- rating_change vs rating_change_weighted:
--   FIDE zeigt beide Werte (chg und K*chg). Beide speichern für flexible Analyse.
--
-- opponent_fide_id: Wird nachträglich per resolve_opponents.py aufgelöst,
--   nicht direkt vom Parser (AJAX-Response enthält keine Gegner-Links).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_results (
    id                      BIGSERIAL PRIMARY KEY,
    fide_id                 INTEGER NOT NULL REFERENCES players(fide_id),
    period                  DATE NOT NULL,
    opponent_name           TEXT,
    opponent_fide_id        INTEGER,                -- per Lookup aufgelöst, initial NULL
    opponent_title          TEXT,                   -- f, m, g, c (FM/IM/GM/CM) oder NULL
    opponent_women_title    TEXT,                   -- wf, wm, wg (WFM/WIM/WGM) oder NULL
    opponent_rating         INTEGER,
    opponent_federation     CHAR(3),                -- z.B. 'GER', 'CHN'; NULL wenn nicht parsebar
    result                  TEXT,                   -- '1' | '0.5' | '0'
    rating_change           NUMERIC(5,2),           -- ungewichtete Änderung (chg)
    rating_change_weighted  NUMERIC(5,2),           -- K * rating_change (K*chg)
    color                   CHAR(1),                -- 'W' | 'B' (Weiss/Schwarz)
    tournament_name         TEXT,
    tournament_location     TEXT,                   -- z.B. 'Moscow RUS'
    tournament_start_date   DATE,
    tournament_end_date     DATE,
    game_index              INTEGER,                -- laufende Nummer innerhalb (fide_id, period)
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fide_id, period, game_index)
);

CREATE INDEX IF NOT EXISTS idx_game_results_fide_period ON game_results (fide_id, period);
CREATE INDEX IF NOT EXISTS idx_game_results_period ON game_results (period);

-- ---------------------------------------------------------------------------
-- rating_history — monatliches Rating pro Spieler
--
-- std_rating:       aus Calculations (Ro in der Summary-Zeile) — Primärquelle
-- published_rating: aus historischen FIDE-TXT-Dateien — Validierung
--
-- Abweichungen zwischen beiden deuten auf Parser-Fehler oder FIDE-Korrekturen hin.
-- Das End-Rating einer Periode ergibt sich aus:
--   std_rating + SUM(game_results.rating_change_weighted)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rating_history (
    fide_id           INTEGER NOT NULL REFERENCES players(fide_id),
    period            DATE NOT NULL,
    std_rating        INTEGER,                      -- Rating aus Calculations (Ro)
    published_rating  INTEGER,                      -- Rating aus FIDE-TXT-Datei
    num_games         INTEGER,
    PRIMARY KEY (fide_id, period)
);
