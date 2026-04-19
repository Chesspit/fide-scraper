#!/usr/bin/env bash
# Forward local port 5434 → VPS TimescaleDB (5432).
# Leave this running in a separate terminal while working in notebooks.
# Port 5434 avoids clash with the local docker-compose dev DB on 5433.
set -euo pipefail
exec ssh -N -L 5434:localhost:5432 pit@187.124.181.116
