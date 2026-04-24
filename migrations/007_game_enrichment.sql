-- Migration 007: game_results enrichment
-- Adds opponent_sex and tournament_type for direct analysis without JOINs.

-- ── opponent_sex ─────────────────────────────────────────────────────────────

ALTER TABLE game_results
    ADD COLUMN IF NOT EXISTS opponent_sex CHAR(1);

-- Stage 1: resolved opponents → sex from players table
UPDATE game_results gr
SET opponent_sex = p.sex
FROM players p
WHERE p.fide_id = gr.opponent_fide_id
  AND gr.opponent_sex IS NULL;

-- Stage 2: unresolved with women's title → female
UPDATE game_results
SET opponent_sex = 'F'
WHERE opponent_fide_id IS NULL
  AND opponent_women_title IS NOT NULL
  AND opponent_sex IS NULL;

-- ── tournament_type ──────────────────────────────────────────────────────────
-- Values: 'open' | 'women' | 'team' | 'women_team'
-- women_team = women-only AND team event (e.g. Women's Olympiad, Women's Bundesliga)

ALTER TABLE game_results
    ADD COLUMN IF NOT EXISTS tournament_type TEXT;

UPDATE game_results SET tournament_type = CASE

    -- Women + Team (both flags apply)
    WHEN (  tournament_name ILIKE '%women%'
         OR tournament_name ILIKE '%woman%'
         OR tournament_name ILIKE '%ladies%'
         OR tournament_name ILIKE '%female%'
         OR tournament_name ILIKE '%girl%'
         OR tournament_name ILIKE '%frauen%'
         OR tournament_name ILIKE '%dame%'
        )
     AND (  tournament_name ILIKE '%olympiad%'
         OR tournament_name ILIKE '%olympic%'
         OR tournament_name ILIKE '%bundesliga%'
         OR tournament_name ILIKE '%team ch%'
         OR tournament_name ILIKE '%team %ch%'
         OR tournament_name ILIKE '%club cup%'
         OR tournament_name ILIKE '%club ch%'
         OR tournament_name ILIKE '%mannschaft%'
         OR tournament_name ILIKE '%nationalliga%'
         OR tournament_name ILIKE '%schachliga%'
         OR tournament_name ILIKE '% smm%'
         OR tournament_name ILIKE 'smm%'
        )
    THEN 'women_team'

    -- Women-only individual events
    WHEN   tournament_name ILIKE '%women%'
        OR tournament_name ILIKE '%woman%'
        OR tournament_name ILIKE '%ladies%'
        OR tournament_name ILIKE '%female%'
        OR tournament_name ILIKE '%girl%'
        OR tournament_name ILIKE '%frauen%'
        OR tournament_name ILIKE '%dame%'
    THEN 'women'

    -- Team events (Olympiad, Bundesliga, Club, League, etc.)
    WHEN   tournament_name ILIKE '%olympiad%'
        OR tournament_name ILIKE '%olympic%'
        OR tournament_name ILIKE '%bundesliga%'
        OR tournament_name ILIKE '%team ch%'
        OR tournament_name ILIKE '%team %ch%'
        OR tournament_name ILIKE '%club cup%'
        OR tournament_name ILIKE '%club ch%'
        OR tournament_name ILIKE '%mannschaft%'
        OR tournament_name ILIKE '%nationalliga%'
        OR tournament_name ILIKE '%schachliga%'
        OR tournament_name ILIKE '% smm%'
        OR tournament_name ILIKE 'smm%'
        OR tournament_name ILIKE '%mannschafts%'
    THEN 'team'

    ELSE 'open'
END;

CREATE INDEX IF NOT EXISTS game_results_tournament_type_idx ON game_results (tournament_type);
CREATE INDEX IF NOT EXISTS game_results_opponent_sex_idx    ON game_results (opponent_sex);
