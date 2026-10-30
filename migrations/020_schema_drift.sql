-- 020: Spalten nachziehen, die nur in der Produktiv-DB existierten (2026-10-30)
--
-- Abgleich information_schema Produktion ↔ frische DB aus 001…019: fünf
-- Spalten wurden irgendwann von Hand angelegt, ohne Migration. Folge: Eine
-- frisch aufgesetzte DB (Tests, Cloud-Session, Neuaufbau) hatte ein anderes
-- Schema; scraper/db.py schreibt scrape_periods.http_status und scheiterte dort.
--
-- Auf der Produktiv-DB ein No-op (IF NOT EXISTS). Typen wie in Produktion.

ALTER TABLE public.scrape_periods ADD COLUMN IF NOT EXISTS http_status INTEGER;

ALTER TABLE public.groups
    ADD COLUMN IF NOT EXISTS federations  TEXT,
    ADD COLUMN IF NOT EXISTS priority     INTEGER,
    ADD COLUMN IF NOT EXISTS scraped_from DATE,
    ADD COLUMN IF NOT EXISTS scraped_to   DATE;
