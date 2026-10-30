"""Frontend-Editionen (Phase 5): EDITION=public registriert interne Seiten nicht.

Läuft die App je Edition in einem eigenen Prozess (wie gunicorn: cwd frontend/,
"import app"), weil Dash Seiten und Callbacks prozessweit registriert.
Geprüft wird nicht nur dash.page_registry, sondern auch /_dash-dependencies:
Callbacks sind eigene HTTP-Endpunkte, eine nur versteckte Seite wäre über
ihre Callbacks weiter erreichbar.

Braucht eine erreichbare DB nur, weil einzelne Seiten beim Import Optionen
laden; Tabellen müssen nicht existieren (Fehler werden dort abgefangen).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
INTERN_ONLY_PATHS = {"/games", "/arpad", "/qc", "/qc-cases", "/qc-corrections"}
INTERN_ONLY_ID_PREFIXES = ("gd-", "arpad-", "qc-", "qcc-")

_PROBE = """
import json, dash, app
client = app.server.test_client()
deps = client.get("/_dash-dependencies").get_json()
outputs = []
for d in deps:
    outputs += [o.split(".")[0].lstrip(".") for o in d["output"].split("..") if o]
print(json.dumps({
    "edition": app.EDITION,
    "paths": sorted(p["path"] for p in dash.page_registry.values()),
    "outputs": sorted(set(outputs)),
    "status_games": client.get("/games").status_code,
}))
"""


def _probe(edition: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "EDITION"}
    env.setdefault("DATABASE_URL", os.environ.get("ORCH_TEST_DATABASE_URL", "postgresql:///none"))
    if edition is not None:
        env["EDITION"] = edition
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=FRONTEND, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def public():
    return _probe("public")


@pytest.fixture(scope="module")
def intern():
    return _probe("intern")


def test_public_registers_no_internal_pages(public):
    assert public["edition"] == "public"
    assert not INTERN_ONLY_PATHS & set(public["paths"])
    assert {"/c", "/dist", "/titles", "/player-profile"} <= set(public["paths"])


def test_public_exposes_no_internal_callbacks(public):
    leaked = [o for o in public["outputs"] if o.startswith(INTERN_ONLY_ID_PREFIXES)]
    assert leaked == []


def test_intern_has_all_pages(intern):
    assert {"/games", "/arpad", "/c", "/dist", "/titles", "/player-profile"} <= set(intern["paths"])
    assert any(o.startswith("gd-") for o in intern["outputs"])
    assert any(o.startswith("arpad-") for o in intern["outputs"])


def test_default_is_public():
    assert _probe(None)["edition"] == "public"


def test_unknown_edition_fails():
    env = {**os.environ, "EDITION": "beta", "DATABASE_URL": "postgresql:///none"}
    out = subprocess.run([sys.executable, "-c", "import app"], cwd=FRONTEND, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode != 0 and "EDITION" in out.stderr
