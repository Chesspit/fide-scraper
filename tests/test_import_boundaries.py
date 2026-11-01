"""Import-Grenzen der vier Blöcke (docs/architektur_vier_bloecke.md, Abschnitt 2).

Erlaubt: jeder Block → core; Frontend, Analysen und Orchestrator-Dashboard → qc.
Verboten: core → irgendein Block; qc → anderer Block als core; niemand → frontend.
Scraper/Orchestrator-Kern (Block 1) importiert weder qc noch frontend — QC
findet, Block 1 repariert; Befunde laufen über die DB, nicht über Importe.

Statisch per ast über alle Module, ohne sie auszuführen. Neue Dateien landen
automatisch in einem Block (BLOCK_RULES); was in keinem landet, schlägt an.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# (Pfadpräfix, Block) — erster Treffer gewinnt, spezifische Pfade zuerst.
BLOCK_RULES = [
    ("core/", "core"),
    ("qc/", "qc"),
    # Orchestrator-Dashboard: darf qc lesen
    ("orchestrator/app.py", "dashboard"),
    ("orchestrator/store.py", "dashboard"),
    ("orchestrator/qc_ui.py", "dashboard"),
    ("orchestrator/qc_pages/", "dashboard"),
    ("orchestrator/", "ingest"),
    ("scraper/", "ingest"),
    ("frontend/", "frontend"),
    ("notebooks/", "analysis"),
    ("scripts/analysis/", "analysis"),
    # QC-CLIs: dünne Hüllen um qc/
    ("scripts/audit_data.py", "qc"),
    ("scripts/quality_check.py", "qc"),
    ("scripts/reconcile_ratings.py", "qc"),
    ("scripts/verify_scrape_integrity.py", "qc"),
    ("scripts/coverage_report.py", "qc"),
    ("scripts/", "ingest"),
]

# Block → erlaubte interne Ziel-Blöcke (Drittanbieter/Stdlib sind immer erlaubt).
ALLOWED = {
    "core":      {"core"},
    "qc":        {"core", "qc"},
    "ingest":    {"core", "ingest"},
    "dashboard": {"core", "qc", "ingest", "dashboard"},
    "frontend":  {"core", "qc", "frontend"},
    "analysis":  {"core", "qc", "analysis"},
}

# Frontend-Module werden flach importiert (gunicorn läuft in frontend/).
FRONTEND_MODULES = {p.stem for p in (ROOT / "frontend").glob("*.py")} | {"pages"}


def block_of(rel: str) -> str | None:
    return next((b for prefix, b in BLOCK_RULES
                 if rel == prefix or (prefix.endswith("/") and rel.startswith(prefix))), None)


def target_block(module: str, importer_block: str) -> str | None:
    """Block eines importierten Moduls, None für Stdlib/Drittanbieter."""
    top = module.split(".")[0]
    if top in ("core", "qc"):
        return top
    if top == "scraper":
        return "ingest"
    if top == "orchestrator":
        return block_of(module.replace(".", "/") + ".py") or block_of(module.replace(".", "/") + "/")
    if top == "scripts":
        return block_of(module.replace(".", "/") + ".py") or "ingest"
    if top == "_setup":
        return "analysis"
    if top in FRONTEND_MODULES and importer_block == "frontend":
        return "frontend"
    if top == "frontend":
        return "frontend"
    return None


def _python_files():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "tests/", "build/")) or "/." in rel:
            continue
        yield rel, path


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            # "from orchestrator import store" → orchestrator.store
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"


def _violations():
    found, unassigned = [], []
    for rel, path in _python_files():
        block = block_of(rel)
        if block is None:
            unassigned.append(rel)
            continue
        for lineno, module in _imports(path):
            target = target_block(module, block)
            if target is not None and target not in ALLOWED[block]:
                found.append(f"{rel}:{lineno} ({block}) importiert {module} ({target})")
    return sorted(set(found)), unassigned


def test_every_module_belongs_to_a_block():
    _, unassigned = _violations()
    assert unassigned == [], f"Kein Block zugeordnet (BLOCK_RULES ergänzen): {unassigned}"


def test_no_forbidden_imports():
    found, _ = _violations()
    assert found == [], "Verbotene Import-Richtungen:\n" + "\n".join(found)


@pytest.mark.parametrize("rel,module,ok", [
    ("core/db.py", "scraper.db", False),
    ("qc/audit.py", "orchestrator.setup_db", False),
    ("orchestrator/worker.py", "qc.audit", False),
    ("orchestrator/app.py", "qc.coverage", True),
    ("frontend/data_chat_queries.py", "qc.rating_views", True),
    ("orchestrator/app.py", "frontend", False),
    ("notebooks/_setup.py", "core.db", True),
    ("scripts/backfill.py", "scraper.db", True),
])
def test_rules_themselves(rel, module, ok):
    block = block_of(rel)
    assert (target_block(module, block) in ALLOWED[block] | {None}) is ok
