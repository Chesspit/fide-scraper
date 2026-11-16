"""WSGI-Einstieg des Steuer-Dashboards für gunicorn (Review F-24).

Im Container:  gunicorn orchestrator.wsgi:server --workers 1 --threads 4 …

Genau EIN Prozess: der Abdeckungs-Tab rechnet in einem Hintergrund-Thread vor
(store.start_coverage_warmer) und hält sein Ergebnis im Speicher dieses
Prozesses — mit mehreren Workern liefe die teure Abfrage mehrfach und jeder
sähe einen anderen Cache.

Lokal zum Entwickeln weiterhin: python -m orchestrator.app (Flask-Server,
FLASK_DEBUG=true für den Reloader).
"""

import logging

from orchestrator import store
from orchestrator.app import app

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
store.start_coverage_warmer()

server = app.server
