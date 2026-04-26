-- Migration 010: groups metadata table
-- Central registry of all analysis groups with status and description.
-- Simplifies the backfill_group.sh workflow and provides a clear overview.

CREATE TABLE IF NOT EXISTS groups (
    group_name      TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    sex             CHAR(1),        -- 'M' | 'F' | NULL (mixed)
    elo_min         INTEGER,
    elo_max         INTEGER,
    sampling        TEXT,           -- 'full_population' | 'age_matched' | 'manual'
    seed            INTEGER,        -- random seed used for sampling
    player_count    INTEGER,        -- current count in players table
    backfill_from   DATE,           -- earliest period scraped
    backfill_to     DATE,           -- latest period scraped
    backfill_status TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'running'|'complete'
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Populate with all current groups
INSERT INTO groups (group_name, description, sex, elo_min, elo_max,
                   sampling, seed, player_count, backfill_from, backfill_to,
                   backfill_status, notes)
VALUES
    ('female_top',
     'Alle aktiven Spielerinnen ELO 2400–2600 (April 2026, vollständige Population)',
     'F', 2400, 2600, 'full_population', NULL, 66,
     '2009-01-01', '2026-03-01', 'complete', 'Inkl. Moser/Khurtsidze nachgetragen 2026-04-24'),

    ('male_control',
     'Age-matched männliche Kontrollgruppe ELO 2400–2600 (3 Erweiterungen)',
     'M', 2400, 2600, 'age_matched', 42, 649,
     '2009-01-01', '2026-03-01', 'running',
     'Seeds 42+43+44+46. Letzte Extension (+170) Backfill läuft 2026-04-26'),

    ('elite_2600',
     'Alle Spieler mit ELO ≥ 2600 — obere Vergleichsschicht',
     NULL, 2600, NULL, 'full_population', NULL, 202,
     '2009-01-01', '2026-03-01', 'complete', NULL),

    ('female_2200',
     'Aktive Spielerinnen ELO 2200–2399 (April 2026)',
     'F', 2200, 2399, 'full_population', NULL, 321,
     '2009-01-01', '2026-03-01', 'complete', NULL),

    ('male_2200',
     'Age-matched männliche Kontrollgruppe ELO 2200–2399 (Gegenstück zu female_2200)',
     'M', 2200, 2399, 'age_matched', 45, 170,
     '2009-01-01', '2026-03-01', 'pending',
     'Definiert 2026-04-26, Backfill noch nicht gestartet'),

    ('swiss_2026',
     'Spieler der Schweizer Mannschaftsmeisterschaft 2026 (NLA+NLB, erste 20 Teams)',
     NULL, NULL, NULL, 'manual', NULL, 349,
     '2009-01-01', '2026-03-01', 'complete',
     'Boolean-Flag swiss_2026 in players, nicht analysis_group')

ON CONFLICT (group_name) DO UPDATE SET
    player_count    = EXCLUDED.player_count,
    backfill_status = EXCLUDED.backfill_status,
    notes           = EXCLUDED.notes,
    updated_at      = NOW();
