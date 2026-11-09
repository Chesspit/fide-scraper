"""Kachelbilder für die Analysen-Übersicht (/analysen) erzeugen.

Zeichnet je Geschichte ein reduziertes Diagramm aus den Parquet-Caches neben
dieser Datei und legt es als PNG unter assets/analysen/<slug>.png ab. Die
Schlagzeile kommt nicht ins Bild, sondern per HTML darüber (pages/analysen.py),
damit Text scharf bleibt und ohne Neuzeichnen geändert werden kann.

    cd frontend && ../.venv/bin/python build_story_covers.py

Die PNGs liegen im Git; neu erzeugen nur, wenn sich ein Motiv ändern soll.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets" / "analysen"

BG = "#101418"
LINE = "#5b6670"
ACCENT = "#ffd23f"


def _canvas():
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
    return fig, ax


def _save(fig, slug: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{slug}.png"
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    print(f"{path.relative_to(HERE)}  {path.stat().st_size // 1024} KB")







def cover_gm_peak() -> None:
    """Typische GM-Karrierekurve, dazu blasse Einzelverläufe."""
    import sys
    sys.path.insert(0, str(HERE))
    import data_gm_peak as gp

    df = gp.load_gm_years()
    c = gp.age_curve(df, True)
    fig, ax = _canvas()
    # Stichprobe über alle Jahrgänge (fest, damit das Bild reproduzierbar bleibt)
    ids = df.drop_duplicates("fide_id").sort_values("birth_year").iloc[::45]["fide_id"]
    for fid in ids:
        p = df[df["fide_id"] == fid].sort_values("age")
        rel = (p["rating"] - p["peak_window"])
        ax.plot(p["age"], rel, color=LINE, lw=0.8, alpha=0.5)
    ax.plot(c["age"], c["curve"], color=ACCENT, lw=3.2)
    ax.set_xlim(12, 72)
    ax.set_ylim(-420, 260)  # oben Platz für die Schlagzeile
    _save(fig, "gm_peak")


def cover_zone2400() -> None:
    """Wege durch die Zone 2400–2600: viele zurück, einer nach oben (Gukesh)."""
    import sys
    sys.path.insert(0, str(HERE))
    import data_zone2400 as z

    s = z.load_entries()
    sample = s.sort_values("entry").iloc[::40]["fide_id"].tolist()
    t = pq.read_table(HERE / "player_history_cache.parquet",
                      filters=[("fide_id", "in", sample + [46616543])]).to_pandas()
    fig, ax = _canvas()
    ax.axhspan(z.LOW, z.HIGH, color="#1d242b", lw=0)
    for fid in sample:
        p = t[t["fide_id"] == fid].sort_values("period")
        e = s.loc[s["fide_id"] == fid, "entry"].iloc[0]
        p = p[p["period"] >= e - pd.DateOffset(months=6)]
        x = (p["period"] - e).dt.days / 365.25
        ax.plot(x, p["rating"], color=LINE, lw=0.9, alpha=0.6)
    g = t[t["fide_id"] == 46616543].sort_values("period")
    e = s.loc[s["fide_id"] == 46616543, "entry"].iloc[0]
    g = g[g["period"] >= e - pd.DateOffset(months=6)]
    ax.plot((g["period"] - e).dt.days / 365.25, g["rating"], color=ACCENT, lw=3.2)
    ax.set_xlim(-0.6, 5.2)
    ax.set_ylim(2250, 3000)  # oben Platz für die Schlagzeile
    _save(fig, "zone2400")



if __name__ == "__main__":
    cover_gm_peak()
    cover_zone2400()
