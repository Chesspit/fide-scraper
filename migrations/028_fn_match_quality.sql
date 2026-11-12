-- Migration 028: fn_match_quality — opponent_match_quality gegen das Periodenrating.
--
-- Bis 11/2026 verglich wide_gap (Migration 009, resolve_opponents.py) das Gegner-Rating der
-- Partie mit dem *heutigen* players.std_rating. Das ist bei Inaktiven 0 und bei alten
-- Perioden weit weg vom damaligen Wert: 2020–2023 waren 36–44 % der zugeordneten Partien
-- „wide_gap“, fast alle zu Unrecht (Stichprobe 2022-06: 81.486 von 81.548).
--
-- Neu: Vergleich mit dem Listenrating des Gegners zur Partie (Vorliste, sonst Liste der
-- Periode, wie load_period_ratings in resolve_opponents.py). FIDE zeigt das Gegner-Rating
-- auf eigenes Rating ± 400 gekappt an — auch 2022-02 bis 2024-03, als die Kappung nicht in
-- die Berechnung einging (fn_fide_diff_cap = NULL). Steht das angezeigte Rating auf der
-- Grenze und liegt das echte jenseits davon, ist die Zuordnung plausibel.
--
--   unresolved  opp_fid IS NULL
--   ok          kein Vergleich möglich (Gegner ohne Listenrating) oder Abstand ≤ 200
--               oder Kappungsfall
--   wide_gap    sonst (Namensvetter wahrscheinlich)

CREATE OR REPLACE FUNCTION fn_match_quality(
    opp_fid           integer,
    opp_rating        integer,   -- game_results.opponent_rating (wie bei FIDE angezeigt)
    opp_period_rating integer,   -- Listenrating des zugeordneten Gegners zur Partie
    own_ro            integer,   -- rating_history.std_rating des Spielers (Ro)
    own_prev          integer    -- published_rating des Spielers in der Vorliste
)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN opp_fid IS NULL THEN 'unresolved'
        WHEN opp_rating IS NULL OR COALESCE(opp_period_rating, 0) = 0 THEN 'ok'
        WHEN abs(opp_rating - opp_period_rating) <= 200 THEN 'ok'
        WHEN opp_period_rating < opp_rating
             AND (abs(opp_rating - (own_ro - 400)) <= 2
                  OR abs(opp_rating - (own_prev - 400)) <= 2) THEN 'ok'
        WHEN opp_period_rating > opp_rating
             AND (abs(opp_rating - (own_ro + 400)) <= 2
                  OR abs(opp_rating - (own_prev + 400)) <= 2) THEN 'ok'
        ELSE 'wide_gap'
    END
$$;
