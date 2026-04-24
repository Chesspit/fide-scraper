-- Migration 008: refine tournament_type with 'closed' and 'knockout' categories
--
-- tournament_type values after this migration:
--   open        – individual Swiss/open-format events
--   closed      – invitation round-robins (Tata Steel, Grand Prix, Candidates…)
--   knockout    – single-elimination (FIDE World Cup)
--   team        – team events (Olympiad, Bundesliga, Club Cup…)
--   women       – women-only individual events
--   women_team  – women-only team events

-- ── Step 1: FIDE World Cup → knockout ────────────────────────────────────────
-- World Cup is a 128-player knockout (K.o.-System), not a round-robin.
-- Women's World Cup stays as 'women' (gender classification takes priority).
UPDATE game_results
SET tournament_type = 'knockout'
WHERE tournament_type = 'open'
  AND tournament_name ILIKE '%world cup%'
  AND tournament_name NOT ILIKE '%women%';

-- ── Step 2: Invitation round-robins → closed ─────────────────────────────────
UPDATE game_results
SET tournament_type = 'closed'
WHERE tournament_type = 'open'
  AND (
      -- Elite supertournaments
      tournament_name ILIKE '%tata steel%'
   OR tournament_name ILIKE '%norway chess%'
   OR tournament_name ILIKE '%sinquefield%'
   OR tournament_name ILIKE '%london classic%'
   OR tournament_name ILIKE '%dortmund%'
   OR tournament_name ILIKE '%zurich chess challenge%'
   OR tournament_name ILIKE '%shamkir%'
   OR tournament_name ILIKE '%tal memorial%'
   OR tournament_name ILIKE '%grenke chess classic%'   -- ≠ GRENKE Chess Open
   OR tournament_name ILIKE '%superbet%'
   OR tournament_name ILIKE '%alashkert%'
   OR tournament_name ILIKE '%wijk aan zee%'
   OR tournament_name ILIKE '%stavanger%'
   OR tournament_name ILIKE '%reggio emilia%'
   OR tournament_name ILIKE '%linares%'
   OR tournament_name ILIKE '%morelia%'
   -- FIDE organised closed series (≠ open Swiss events with "Grand Prix" in name)
   OR (tournament_name ILIKE '%fide grand prix%'
       AND tournament_name NOT ILIKE '%women%')
   -- Candidates Tournament
   OR tournament_name ILIKE '%candidates%'
   -- Biel Masters (MTO) – the invitation section, ≠ Biel Open
   OR tournament_name ILIKE '%biel master%'
   OR tournament_name ILIKE '%int. chess festival biel master%'
  );
