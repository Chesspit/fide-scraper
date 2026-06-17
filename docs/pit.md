# Pit's Projektnotizen — ELO-Einsichten

Stand: 2026-06-17

---

## Offene Punkte / Ideen

### Analytics-Frontend (scelo.chesspit.net/analytics)
- [ ] Textbox mit Einführung / Erklärung pro Seite
- [ ] Überarbeitung Legenden und Achsenbeschriftungen
- [ ] Verbesserung Dropdown-Menü
- [ ] Mehrsprachigkeit: Deutsch + Englisch (+ später Spanisch)
- [ ] Domain / Branding: „ELO-Einsichten" (DE) / „ELO-Insights" (EN)

### Analyse-Ideen
- [ ] Zeitliche Entwicklung der Anzahl weiblicher Spieler
- [ ] Gaußkurve: theoretisch zu erwartende Anzahl starker Spielerinnen
- [ ] Alters-Rating-Kurven (Kohortenanalyse)
- [ ] Betrachtungen nach Land / Föderation
- [ ] Quantil-ELO-Verlauf: Welches Quantil hatte welche ELO im Verlauf der Zeit?
- [ ] Ausweis ELO-Korrektur 2024 (Einmalige +0.4×(2000−rating))
- [ ] Inaktive/verstorbene Spitzenspielerinnen
- [ ] Artikel/Analyse: Vergleich weibliche TOP-Spieler vs. männliche Gleichstarke

### Datenbasis / Qualität
- [ ] Namen, Geschlecht, Föderation aus FIDE-TXT verfeinern
- [ ] Kontrollroutinen: Partiedaten erklären ELO-Entwicklung (QC)
- [ ] no_data differenzieren: kein Turnier vs. Periode nicht verfügbar

### Ausweitung Scraping
- [ ] Rapid und Blitz (und Freestyle)
- [ ] female_1900 / female_1800 nach Abschluss der laufenden Chain
- [ ] Alle 22.738 Spielerinnen langfristig: female_2000/1800/1600/1400

### Orchestrator / Infrastruktur
- [ ] Übersicht-Tab: In Zellen gescrapte Jahre anschreiben (statt nur Status-Farbe)
- [ ] Queue-Reihenfolge optimieren
- [ ] Vorab-Filter: Spielmonate aus TXT-Snapshot → unnötige Requests sparen
- [ ] Auto-Retry: failed-Gruppen nach X Stunden automatisch auf pending zurücksetzen
- [ ] Laptop-Setup: SSH-Key → VPS, Repo clonen, Claude Code installieren
- [ ] Datacenter-Proxy-Test: 1-2 GB DC kaufen, conservative Profil testen

---

## Erledigtes (Session 2026-06-17)

✅ female_1800_11–18 abgeschlossen (Mac Mini Chain, fertig 16:42 Uhr)
✅ female_1800_19 abgeschlossen (gestartet 17:07 Uhr, fertig 20:35 Uhr — 8617/8617, 0 Fehler)
✅ Spieler mit ≥1 Periode: 140.168 (vs. 95.585 beim dc_update-Start am 2026-06-07, +44.583)
✅ DB-Dump erstellt (backups/fidedb_2026-06-17.dump, 789 MB, pg_dump -F c)
✅ docs/scraping_status.md + docs/project_status.md auf aktuellen Stand gebracht

## Erledigtes (Session 2026-05-25)

✅ DC-ES/MX/AE eingerichtet und aktiviert
✅ Bericht-Tab im Orchestrator-Dashboard (MB/Tag pro Thread, Residential/DC-Summen)
✅ Analytics-Navbar restrukturiert (Aktiv/Test-Gruppen, deutsche Flagge)
✅ Version A/B gelöscht, ELO-Top100 umbenannt
✅ Default-Spieler Gukesh D auf allen Seiten
✅ Worker-Neustart nach HK-Abschluss automatisiert (17:14 Uhr)
