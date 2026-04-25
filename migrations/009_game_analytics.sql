-- Migration 009: analytical columns on game_results
--
-- A2: expected_score + over_performance  (Performance vs. Elo-Erwartung)
-- B2: opponent_match_quality             (Wide-gap match flag)

-- ── A2: Performance vs. Erwartung ────────────────────────────────────────────
-- expected_score = 1 / (1 + 10^((opp_rating - own_rating) / 400))
-- over_performance = actual_result - expected_score
-- Positive = besser als erwartet, negativ = schlechter als erwartet.
-- own_rating kommt aus rating_history.std_rating (Ro der Periode).

ALTER TABLE game_results
    ADD COLUMN IF NOT EXISTS expected_score    NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS over_performance  NUMERIC(6,4);

UPDATE game_results gr
SET
    expected_score   = ROUND(
        1.0 / (1.0 + POWER(10.0, (gr.opponent_rating - rh.std_rating) / 400.0))
    , 4),
    over_performance = ROUND(
        CAST(gr.result AS NUMERIC)
        - 1.0 / (1.0 + POWER(10.0, (gr.opponent_rating - rh.std_rating) / 400.0))
    , 4)
FROM rating_history rh
WHERE rh.fide_id = gr.fide_id
  AND rh.period  = gr.period
  AND gr.opponent_rating IS NOT NULL
  AND rh.std_rating      IS NOT NULL
  AND gr.result          IS NOT NULL;

CREATE INDEX IF NOT EXISTS game_results_over_performance_idx
    ON game_results (over_performance);

-- ── B2: Opponent match quality ────────────────────────────────────────────────
-- ok          – opponent_fide_id aufgelöst, Rating-Differenz ≤ 200
-- wide_gap    – aufgelöst, aber |opponent_rating - players.std_rating| > 200
--               (möglicherweise falsche Zuordnung durch Namenskollision)
-- unresolved  – opponent_fide_id ist NULL

ALTER TABLE game_results
    ADD COLUMN IF NOT EXISTS opponent_match_quality TEXT;

-- unresolved
UPDATE game_results
SET opponent_match_quality = 'unresolved'
WHERE opponent_fide_id IS NULL;

-- ok vs wide_gap
UPDATE game_results gr
SET opponent_match_quality = CASE
    WHEN ABS(gr.opponent_rating - p.std_rating) > 200 THEN 'wide_gap'
    ELSE 'ok'
END
FROM players p
WHERE p.fide_id = gr.opponent_fide_id
  AND gr.opponent_fide_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS game_results_match_quality_idx
    ON game_results (opponent_match_quality);
