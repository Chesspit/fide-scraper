-- Add swiss_2026 flag so a player can belong to both swiss_players and an
-- existing analysis_group (female_top / male_control / elite_2600).
ALTER TABLE players ADD COLUMN IF NOT EXISTS swiss_2026 BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS players_swiss_2026 ON players (swiss_2026) WHERE swiss_2026 = TRUE;
