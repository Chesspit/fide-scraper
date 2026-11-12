-- Migration 029: fn_game_expected — expected_score aus den FIDE-Daten statt logistischer Formel.
--
-- Bis 11/2026 (Migration 009, resolve_opponents.py) war
--   expected_score = 1 / (1 + 10^((Gegner − Ro) / 400))
-- Das ist nicht die FIDE-Rechnung (Tabelle 8.1.2, 400-Punkte-Regel, Migration 018) und nutzt
-- ein einziges Ro je Periode, obwohl Ro bei mehreren Turnieren je Turnier verschieden ist.
--
-- FIDE weist je Partie rating_change = Ergebnis − Erwartungswert aus (ungewichtet). Daraus
-- folgt der Erwartungswert exakt, auch bei mehreren Turnieren je Periode. Prüfung 2021-06
-- (12.11.2026): Einzelturnier-Perioden 40.436 Partien, 0 Abweichungen zur FIDE-Tabelle;
-- die logistische Formel lag im Mittel 0,005 daneben (max 0,015), bei mehreren Turnieren
-- bis 0,25.
--
-- Regeln:
--   Ro fehlt (Erstbewertung, Partie fließt in keine Bewertung ein) → NULL
--   Ergebnis nicht 0 / 0.5 / 1                                     → NULL
--   rating_change vorhanden                                        → Ergebnis − rating_change
--   sonst FIDE-Tabelle gegen Ro mit 400-Punkte-Regel (wie qc/audit.py)
-- over_performance = Ergebnis − expected_score (= rating_change, wo vorhanden).

CREATE OR REPLACE FUNCTION fn_game_expected(
    result           text,
    rating_change    numeric,
    own_ro           integer,
    opp_rating       integer,
    tournament_start date
)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN own_ro IS NULL OR result IS NULL OR result NOT IN ('0', '0.5', '1') THEN NULL
        WHEN rating_change IS NOT NULL THEN result::numeric - rating_change
        WHEN opp_rating IS NULL THEN NULL
        ELSE fn_fide_expected(
            CASE WHEN fn_fide_diff_cap(tournament_start) IS NULL THEN own_ro - opp_rating
                 ELSE GREATEST(-fn_fide_diff_cap(tournament_start),
                               LEAST(fn_fide_diff_cap(tournament_start), own_ro - opp_rating))
            END)
    END
$$;
