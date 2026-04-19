-- Analysis views for female_top vs. male_control comparison.
-- All views filter p.active = TRUE to exclude players FIDE currently marks
-- as inactive (Flag 'i'/'wi'). See CLAUDE.md → "Bekannte Limitationen".

-- Opponent structure: average opponent rating vs. own rating per (player, period).
CREATE OR REPLACE VIEW v_opponent_strength AS
SELECT
    p.analysis_group,
    gr.fide_id,
    gr.period,
    rh.std_rating AS own_rating,
    AVG(gr.opponent_rating) AS avg_opponent_rating,
    AVG(gr.opponent_rating) - rh.std_rating AS avg_opponent_diff
FROM game_results gr
JOIN players p USING (fide_id)
LEFT JOIN rating_history rh ON rh.fide_id = gr.fide_id AND rh.period = gr.period
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
GROUP BY p.analysis_group, gr.fide_id, gr.period, rh.std_rating;

-- Rating volatility: mean absolute rating change per period, normalized by K-factor.
CREATE OR REPLACE VIEW v_rating_volatility AS
SELECT
    p.analysis_group,
    gr.fide_id,
    gr.period,
    sp.k_factor,
    AVG(ABS(gr.rating_change)) AS avg_abs_change,
    CASE WHEN sp.k_factor > 0
         THEN AVG(ABS(gr.rating_change)) / sp.k_factor
         ELSE NULL END AS normalized_volatility
FROM game_results gr
JOIN players p USING (fide_id)
LEFT JOIN scrape_periods sp ON sp.fide_id = gr.fide_id AND sp.period = gr.period
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
GROUP BY p.analysis_group, gr.fide_id, gr.period, sp.k_factor;

-- Tournament frequency: games and tournaments per (player, period).
CREATE OR REPLACE VIEW v_tournament_frequency AS
SELECT
    p.analysis_group,
    gr.fide_id,
    gr.period,
    COUNT(*) AS num_games,
    COUNT(DISTINCT gr.tournament_name) AS num_tournaments
FROM game_results gr
JOIN players p USING (fide_id)
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
GROUP BY p.analysis_group, gr.fide_id, gr.period;

-- Rating progression: rating per period + delta vs. first observation for the player.
CREATE OR REPLACE VIEW v_rating_progression AS
SELECT
    p.analysis_group,
    rh.fide_id,
    rh.period,
    rh.std_rating,
    rh.std_rating - FIRST_VALUE(rh.std_rating)
        OVER (PARTITION BY rh.fide_id ORDER BY rh.period) AS rating_delta_from_start
FROM rating_history rh
JOIN players p USING (fide_id)
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
ORDER BY rh.fide_id, rh.period;
