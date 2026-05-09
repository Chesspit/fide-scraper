-- Migration 012: dynamic group membership view
--
-- The static players.analysis_group freezes the April-2026 ELO snapshot.
-- This view assigns groups per period based on published_rating, so a player
-- is only in e.g. 'female_top' for the months they actually had 2400–2600.
--
-- Key differences from static analysis_group:
--   - No p.active filter: a player retired in 2020 was genuinely active earlier
--   - published_rating IS NOT NULL is the "active that period" proxy
--   - static_group column included for easy static vs. dynamic comparison

CREATE OR REPLACE VIEW v_dynamic_membership AS
SELECT
    rh.fide_id,
    rh.period,
    p.sex,
    p.analysis_group                                                    AS static_group,
    rh.published_rating,
    CASE
        WHEN rh.published_rating >= 2600                                THEN 'elite_2600'
        WHEN p.sex = 'F' AND rh.published_rating BETWEEN 2400 AND 2600 THEN 'female_top'
        WHEN p.sex = 'M' AND rh.published_rating BETWEEN 2400 AND 2600 THEN 'male_control'
        WHEN p.sex = 'F' AND rh.published_rating BETWEEN 2200 AND 2399 THEN 'female_2200'
        WHEN p.sex = 'M' AND rh.published_rating BETWEEN 2200 AND 2399 THEN 'male_2200'
        ELSE NULL
    END                                                                 AS dynamic_group
FROM rating_history rh
JOIN players p USING (fide_id)
WHERE rh.published_rating IS NOT NULL;
