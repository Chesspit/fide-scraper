"""marimo-Notebooks (Phase 6): ohne DB nicht ausführbar, deshalb statisch prüfen.

Je Notebook: `marimo check` ohne Befund, Zellgraph ohne Mehrfachdefinitionen/
Zyklen, und die Importe der Setup-Zelle (_setup → core) funktionieren.
Ausführung und Ergebnisvergleich mit den .ipynb passieren lokal.
"""

import subprocess
import sys
from pathlib import Path

import pytest

marimo = pytest.importorskip("marimo")

NOTEBOOKS = Path(__file__).resolve().parent.parent / "notebooks"
MARIMO_FILES = sorted(
    p for p in NOTEBOOKS.glob("[0-9]*.py") if "marimo.App" in p.read_text(encoding="utf-8")
)


def test_there_are_marimo_notebooks():
    assert {p.stem[:2] for p in MARIMO_FILES} >= {"14", "15"}


@pytest.mark.parametrize("path", MARIMO_FILES, ids=lambda p: p.stem)
def test_marimo_check(path):
    out = subprocess.run([sys.executable, "-m", "marimo", "check", str(path)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("path", MARIMO_FILES, ids=lambda p: p.stem)
def test_cell_graph(path):
    from marimo._ast.load import load_app

    app = load_app(str(path))
    graph = app._graph if hasattr(app, "_graph") else app._app.graph
    assert not graph.get_multiply_defined()
    assert not graph.cycles


def test_setup_imports():
    code = "import _setup; assert _setup.elo_band(2449) == '2400-2449'; _setup.get_conn"
    out = subprocess.run([sys.executable, "-c", code], cwd=NOTEBOOKS,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
