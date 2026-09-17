-- Migration 018: FIDE-Erwartungswert (Tabelle 8.1.2) + 400-Punkte-Regel als SQL-Funktionen
--
-- Warum: scripts/audit_data.py prüft jede gescrapte Partie gegen die Elo-Formel
--   rating_change = Ergebnis − E(Ro − Gegnerrating)
-- Damit fallen Parser-/Spaltenverschiebungen auf, ohne dass eine externe
-- Referenz nötig ist. Die Funktionen sind bewusst allgemein gehalten, damit auch
-- Notebooks sie nutzen können.
--
-- Verifiziert am 2026-09-16 gegen die Produktivdaten: 509.691 Partien aus
-- 2021-11, 2022-09, 2023-03, 2024-11, 2025-10 (nur Perioden mit genau einem
-- Turnier, weil die Summary-Zeile nur EIN Ro liefert) — 0 Abweichungen,
-- keine einzige mehrdeutige Differenz. Die Tabelle ist die FIDE-Tabelle aus dem
-- Rating-Regelwerk, NICHT die gerundete Normalverteilung (die weicht an den
-- Grenzen 25/32, 53/61, … um einen Punkt ab).
--
-- 400-Punkte-Regel (fn_fide_diff_cap): aus denselben Daten abgeleitet, nicht aus
-- der Doku übernommen. Maßgeblich ist der TURNIERBEGINN, nicht die Periode
-- (Turniere über den Monatswechsel werden in der Folgeperiode nach der alten
-- Regel gerechnet). Partien mit |Δ| > 400 aus Einzelturnier-Perioden,
-- 2021-11 bis 2022-05 und 2024-01 bis 2024-08:
--   Turnierbeginn 2022-01 … 2024-02  → 100 % ungekappt (57.902 Partien)
--   Turnierbeginn davor / danach     → |Δ| > 400 kommt gar nicht vor: FIDE zeigt
--                                      das Gegnerrating dann schon auf Ro ± 400 begrenzt an
-- Vor 2020 nicht verifiziert; dort wird die Kappung angenommen.
--
-- IMMUTABLE + PARALLEL SAFE wie fn_elo_band() (Migration 017).
-- Python-Zwilling: orchestrator/audit.py::fide_expected() / diff_cap(),
-- tests/test_audit.py prüft beide gegeneinander.

CREATE OR REPLACE FUNCTION fn_fide_expected(diff INTEGER)
    RETURNS NUMERIC
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
AS $$
    -- Erwartungswert aus Sicht des Spielers mit Differenz diff = Ro − Gegner.
    -- Negative Differenz: Gegenwahrscheinlichkeit des Höhergewerteten.
    SELECT CASE WHEN diff IS NULL THEN NULL ELSE
        CASE WHEN diff >= 0 THEN e ELSE 1 - e END
    END
    FROM (SELECT CASE
        WHEN a <=   3 THEN 0.50 WHEN a <=  10 THEN 0.51 WHEN a <=  17 THEN 0.52
        WHEN a <=  25 THEN 0.53 WHEN a <=  32 THEN 0.54 WHEN a <=  39 THEN 0.55
        WHEN a <=  46 THEN 0.56 WHEN a <=  53 THEN 0.57 WHEN a <=  61 THEN 0.58
        WHEN a <=  68 THEN 0.59 WHEN a <=  76 THEN 0.60 WHEN a <=  83 THEN 0.61
        WHEN a <=  91 THEN 0.62 WHEN a <=  98 THEN 0.63 WHEN a <= 106 THEN 0.64
        WHEN a <= 113 THEN 0.65 WHEN a <= 121 THEN 0.66 WHEN a <= 129 THEN 0.67
        WHEN a <= 137 THEN 0.68 WHEN a <= 145 THEN 0.69 WHEN a <= 153 THEN 0.70
        WHEN a <= 162 THEN 0.71 WHEN a <= 170 THEN 0.72 WHEN a <= 179 THEN 0.73
        WHEN a <= 188 THEN 0.74 WHEN a <= 197 THEN 0.75 WHEN a <= 206 THEN 0.76
        WHEN a <= 215 THEN 0.77 WHEN a <= 225 THEN 0.78 WHEN a <= 235 THEN 0.79
        WHEN a <= 245 THEN 0.80 WHEN a <= 256 THEN 0.81 WHEN a <= 267 THEN 0.82
        WHEN a <= 278 THEN 0.83 WHEN a <= 290 THEN 0.84 WHEN a <= 302 THEN 0.85
        WHEN a <= 315 THEN 0.86 WHEN a <= 328 THEN 0.87 WHEN a <= 344 THEN 0.88
        WHEN a <= 357 THEN 0.89 WHEN a <= 374 THEN 0.90 WHEN a <= 391 THEN 0.91
        WHEN a <= 411 THEN 0.92 WHEN a <= 432 THEN 0.93 WHEN a <= 456 THEN 0.94
        WHEN a <= 484 THEN 0.95 WHEN a <= 517 THEN 0.96 WHEN a <= 559 THEN 0.97
        WHEN a <= 619 THEN 0.98 WHEN a <= 735 THEN 0.99
        ELSE 1.00 END AS e
        FROM (SELECT abs(diff) AS a) s
    ) t;
$$;

COMMENT ON FUNCTION fn_fide_expected(INTEGER) IS
    'FIDE-Erwartungswert (Tabelle 8.1.2) für Differenz Ro − Gegner, z.B. 95 -> 0.63, -95 -> 0.37. '
    'Kappung NICHT enthalten, siehe fn_fide_diff_cap(). Spiegelbild: orchestrator/audit.py::fide_expected().';

CREATE OR REPLACE FUNCTION fn_fide_diff_cap(tournament_start DATE)
    RETURNS INTEGER
    LANGUAGE sql
    IMMUTABLE
    PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN tournament_start >= DATE '2022-01-01'
         AND tournament_start <  DATE '2024-03-01' THEN NULL
        ELSE 400
    END;
$$;

COMMENT ON FUNCTION fn_fide_diff_cap(DATE) IS
    'Kappungsgrenze der Rating-Differenz nach Turnierbeginn: 400, bzw. NULL (keine Kappung) '
    'für Turniere mit Beginn 2022-01 bis 2024-02. Ohne Startdatum die Periode übergeben. '
    'Spiegelbild: orchestrator/audit.py::diff_cap().';
