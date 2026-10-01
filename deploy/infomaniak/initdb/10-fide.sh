#!/usr/bin/env bash
# Läuft nur beim allerersten Start auf leerem Volume (docker-entrypoint-initdb.d).
# Legt Rolle + Datenbank für fide-scraper an; die Daten kommen danach per
# pg_restore (Reihenfolge siehe docs/umzug_infomaniak.md).
set -euo pipefail
: "${FIDE_DB_PASSWORD:?FIDE_DB_PASSWORD fehlt}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v pw="$FIDE_DB_PASSWORD" <<'SQL'
CREATE ROLE fide LOGIN PASSWORD :'pw';
CREATE DATABASE fidedb OWNER fide;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname fidedb <<'SQL'
CREATE EXTENSION IF NOT EXISTS timescaledb;
SQL
