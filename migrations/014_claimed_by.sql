-- 014: claimed_by für Multi-Device-Queue (Redesign Phase B, 2026-07-07)
--
-- Jeder Claim schreibt die Geräte-Identität (Env WORKER_DEVICE_ID, Default
-- Hostname) in claimed_by; reset_stale_running() setzt beim Worker-Start nur
-- noch die EIGENEN running-Gruppen zurück. Damit können mehrere Geräte
-- (VPS-Worker, Raspberry Pi, Mac Mini) dieselbe Queue teilen, ohne sich beim
-- Start gegenseitig die laufenden Gruppen abzuräumen.
--
-- setup_db.ensure_schema() spiegelt dieses DDL idempotent — auf dem VPS wird
-- die Spalte beim ersten Worker-/Dashboard-Start mit neuem Code automatisch
-- angelegt; dieses Skript dokumentiert die Änderung im Migrationspfad.

ALTER TABLE orchestrator.scrape_groups ADD COLUMN IF NOT EXISTS claimed_by TEXT;
