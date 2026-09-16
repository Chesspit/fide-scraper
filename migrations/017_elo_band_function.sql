-- Migration 017: zentrale ELO-Band-Klassifikation als SQL-Funktion
--
-- Warum: Die Bandlogik existierte bisher dreifach nebeneinander —
--   * notebooks/_generate_13.py und _generate_14.py: je eine eigene pandas-Funktion
--     `elo_band(r)` mit 50er-Schritten (wortgleich dupliziert),
--   * orchestrator/coverage.py: eigener band_width-Parameter (Default 100),
--   * frontend/data_qc.py: gar keine, dort wird nur nach players.analysis_group
--     gefiltert.
-- Drei Definitionen, die auseinanderlaufen können, sobald eine davon angepasst
-- wird. Diese Funktionen sind ab jetzt die eine Quelle; der pandas-Helper in
-- notebooks/_setup.py::elo_band() spiegelt sie bewusst 1:1 (mit Test dagegen,
-- siehe tests/test_elo_bands.py).
--
-- 50er-Breite, nicht 100er: entspricht der bereits etablierten Konvention in
-- Notebook 13/14 und der ±50-Stärke-Schwelle, die docs/project_status.md 6.7
-- als Projekt-Standard festhält. Gröbere Schnitte lassen sich jederzeit aus
-- fn_elo_band() herausrechnen (band_floor / 100), umgekehrt nicht.
--
-- Bewusst KEINE Sammeltöpfe an den Rändern (kein "u1000"/"2600plus"): Notebook 14
-- wertet die rohen Bänder aus, ein Zusammenfassen der Enden würde dessen Zahlen
-- verfälschen. Wer aggregieren will, tut es in der Abfrage.
--
-- Bewusst KEINE View obendrauf: Notebook 14 leitet seine Studienkohorte per
-- eigener SQL-CTE aus rating_history ab (Top-40-Frauen je Jahresende + Männer im
-- Band 2400–2600). Eine zweite, view-basierte Kohortendefinition daneben würde
-- nur die Frage aufwerfen, welche von beiden gilt.
--
-- v_dynamic_membership (Migration 012) bleibt unverändert bestehen: sie ist
-- dormant (im Repo nirgends abgefragt), aber in der Doku referenziert und
-- klassifiziert nur ELO >= 2200 in fünf feste Kohortennamen. Diese Funktionen
-- ersetzen sie nicht, sie decken einen anderen Zweck ab (volles Spektrum,
-- neutrale Bandnamen, keine Forschungssemantik).
--
-- IMMUTABLE + PARALLEL SAFE: reine Arithmetik ohne Tabellenzugriff — damit darf
-- der Planner die Aufrufe faltbar behandeln und in parallelen Plänen verwenden,
-- und die Funktionen sind in Index-Ausdrücken nutzbar, falls das je nötig wird.

CREATE OR REPLACE FUNCTION fn_elo_band(rating INTEGER)
    RETURNS INTEGER
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
AS $$
    SELECT CASE WHEN rating IS NULL THEN NULL ELSE (rating / 50) * 50 END;
$$;

COMMENT ON FUNCTION fn_elo_band(INTEGER) IS
    'Untergrenze des 50er-ELO-Bands (2449 -> 2400). NULL bei NULL-Rating. '
    'Spiegelbild: notebooks/_setup.py::elo_band_floor().';

CREATE OR REPLACE FUNCTION fn_elo_group(rating INTEGER, sex CHAR(1))
    RETURNS TEXT
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
AS $$
    SELECT CASE WHEN rating IS NULL THEN NULL ELSE
        -- Unbekanntes/fehlendes Geschlecht wird zu 'x' statt zu NULL: betrifft
        -- ~2.015 von 1,8 Mio Spielern (0,1 %). Als eigenes Präfix bleiben sie in
        -- Auswertungen sichtbar, statt stillschweigend herauszufallen.
        CASE lower(coalesce(sex, 'x'))
            WHEN 'f' THEN 'f'
            WHEN 'm' THEN 'm'
            ELSE 'x'
        END
        || '_' || ((rating / 50) * 50)::text
        || '_' || ((rating / 50) * 50 + 49)::text
    END;
$$;

COMMENT ON FUNCTION fn_elo_group(INTEGER, CHAR) IS
    'Geschlecht + 50er-ELO-Band als Bezeichner, z.B. (2455, ''F'') -> f_2450_2499. '
    'NULL bei NULL-Rating; unbekanntes Geschlecht -> Präfix x_.';
