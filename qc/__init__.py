"""Block 4 — Qualitätskontrolle (docs/architektur_vier_bloecke.md).

Bibliothek: Prüflogik und QC-Abfragen. Oberflächen (Orchestrator-Dashboard,
Frontend, Notebooks) und CLIs in scripts/ rufen qc/ auf und enthalten kein
eigenes QC-SQL.

Grenzen: qc importiert nur core (und keinen anderen Block). QC schreibt nie in
Rohdaten-Tabellen, nur in eigene Ergebnistabellen (qc_rating_check,
qc_audit_runs) und Berichte. Reparaturen sind Aufträge an Block 1.
"""
