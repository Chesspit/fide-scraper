-- Migration 026: fn_tournament_type(name) — Turniertyp aus dem Turniernamen.
--
-- Dieselbe Regel wie die einmaligen UPDATEs aus Migration 007 + 008, jetzt als Funktion,
-- damit scripts/resolve_opponents.py neue Partien periodenweise anreichern kann
-- (bis 11/2026 war tournament_type nur für die ~0,5 Mio Partien von April 2026 gesetzt).
-- Werte: 'open' | 'closed' | 'knockout' | 'women' | 'team' | 'women_team'

CREATE OR REPLACE FUNCTION fn_tournament_type(name text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN is_women AND is_team THEN 'women_team'
        WHEN is_women             THEN 'women'
        WHEN is_team              THEN 'team'
        WHEN is_knockout          THEN 'knockout'
        WHEN is_closed            THEN 'closed'
        ELSE 'open'
    END
    FROM (
        SELECT
            (   name ILIKE '%women%'
             OR name ILIKE '%woman%'
             OR name ILIKE '%ladies%'
             OR name ILIKE '%female%'
             OR name ILIKE '%girl%'
             OR name ILIKE '%frauen%'
             OR name ILIKE '%dame%') IS TRUE AS is_women,
            (   name ILIKE '%olympiad%'
             OR name ILIKE '%olympic%'
             OR name ILIKE '%bundesliga%'
             OR name ILIKE '%team ch%'
             OR name ILIKE '%team %ch%'
             OR name ILIKE '%club cup%'
             OR name ILIKE '%club ch%'
             OR name ILIKE '%mannschaft%'
             OR name ILIKE '%nationalliga%'
             OR name ILIKE '%schachliga%'
             OR name ILIKE '% smm%'
             OR name ILIKE 'smm%') IS TRUE AS is_team,
            -- 008: FIDE World Cup (Frauen-World-Cup bleibt 'women')
            (name ILIKE '%world cup%') IS TRUE AS is_knockout,
            -- 008: Einladungs-Rundenturniere
            (   name ILIKE '%tata steel%'
             OR name ILIKE '%norway chess%'
             OR name ILIKE '%sinquefield%'
             OR name ILIKE '%london classic%'
             OR name ILIKE '%dortmund%'
             OR name ILIKE '%zurich chess challenge%'
             OR name ILIKE '%shamkir%'
             OR name ILIKE '%tal memorial%'
             OR name ILIKE '%grenke chess classic%'
             OR name ILIKE '%superbet%'
             OR name ILIKE '%alashkert%'
             OR name ILIKE '%wijk aan zee%'
             OR name ILIKE '%stavanger%'
             OR name ILIKE '%reggio emilia%'
             OR name ILIKE '%linares%'
             OR name ILIKE '%morelia%'
             OR name ILIKE '%fide grand prix%'
             OR name ILIKE '%candidates%'
             OR name ILIKE '%biel master%') IS TRUE AS is_closed
    ) f
$$;
