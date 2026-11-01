"""Fundament aller vier Blöcke (docs/architektur_vier_bloecke.md).

core importiert keinen Block — weder scraper, orchestrator, frontend, qc noch
notebooks. Jeder Block darf core importieren. tests/test_import_boundaries.py
wacht darüber.
"""
