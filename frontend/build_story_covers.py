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
CARLSEN = 1503014


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


def cover_top100() -> None:
    df = pd.read_parquet(HERE / "top100_cache.parquet")
    fig, ax = _canvas()
    for rank in range(2, 101, 3):
        s = df[df["rank"] == rank].sort_values("period")
        ax.plot(s["period"], s["rating"], color=LINE, lw=0.8, alpha=0.6)
    top = df[df["rank"] == 1].sort_values("period")
    ax.plot(top["period"], top["rating"], color=ACCENT, lw=3.2)
    lo, hi = df["rating"].min(), top["rating"].max()
    ax.set_ylim(lo - 20, hi + (hi - lo) * 0.45)  # oben Platz für die Schlagzeile
    ax.margins(x=0.02)
    _save(fig, "top100")


def cover_dist() -> None:
    df = pd.read_parquet(HERE / "percentile_cache.parquet")
    scope = df.groupby("scope")["n_players"].max().idxmax()
    s = df[df["scope"] == scope].sort_values("period")
    fig, ax = _canvas()
    bands = [("p5", "p95", 0.18), ("p10", "p90", 0.22), ("p20", "p80", 0.28), ("p30", "p70", 0.36)]
    for lo, hi, alpha in bands:
        ax.fill_between(s["period"], s[lo], s[hi], color="#9fb3c4", alpha=alpha, lw=0)
    ax.plot(s["period"], s["p50"], color=ACCENT, lw=3)
    ax.plot(s["period"], s["p99"], color=LINE, lw=1.2, ls="--")
    ax.margins(x=0.02, y=0.2)
    _save(fig, "dist")


def cover_titles() -> None:
    df = pd.read_parquet(HERE / "title_cache.parquet")
    last = df[df["year"] == df["year"].max()].nlargest(10, "gm").iloc[::-1]
    fig, ax = _canvas()
    colors = [LINE] * (len(last) - 1) + [ACCENT]
    ax.barh(range(len(last)), last["gm"], color=colors, height=0.62)
    ax.set_xlim(-last["gm"].max() * 0.9, last["gm"].max() * 1.08)  # links Platz für die Schlagzeile
    ax.set_ylim(-0.8, len(last) + 2.2)  # oben Platz für die Schlagzeile
    _save(fig, "titles")


def cover_player() -> None:
    t = pq.read_table(HERE / "player_history_cache.parquet", filters=[("fide_id", "=", CARLSEN)])
    s = t.to_pandas().sort_values("period")
    fig, ax = _canvas()
    ax.fill_between(s["period"], s["rating"].min() - 40, s["rating"], color=ACCENT, alpha=0.12, lw=0)
    ax.plot(s["period"], s["rating"], color=ACCENT, lw=3)
    lo, hi = s["rating"].min() - 40, s["rating"].max()
    ax.set_ylim(lo, hi + (hi - lo) * 0.9)  # oben Platz für die Schlagzeile
    ax.margins(x=0.02)
    _save(fig, "player")


def cover_games() -> None:
    """Rating-Änderung je Liste (Carlsen) als Plus/Minus-Balken."""
    t = pq.read_table(HERE / "player_history_cache.parquet", filters=[("fide_id", "=", CARLSEN)])
    s = t.to_pandas().sort_values("period")
    d = s["rating"].diff().dropna().iloc[-60:]
    fig, ax = _canvas()
    ax.bar(range(len(d)), d, color=[ACCENT if v >= 0 else LINE for v in d], width=0.7)
    m = d.abs().max()
    ax.set_ylim(-m * 1.4, m * 2.6)
    ax.margins(x=0.02)
    _save(fig, "games")


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


def cover_arpad() -> None:
    """Zwei Sprechblasen — Frage und Antwort."""
    from matplotlib.patches import FancyBboxPatch, Polygon

    fig, ax = _canvas()
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    for x, y, w, color, tail in [(1.2, 1.2, 8.2, LINE, "left"), (6.6, 3.9, 8.2, ACCENT, "right")]:
        ax.add_patch(FancyBboxPatch((x, y), w, 1.9, boxstyle="round,pad=0.25,rounding_size=0.8",
                                    fc=color, ec="none", alpha=0.9))
        tx = x + 0.9 if tail == "left" else x + w - 0.9
        ax.add_patch(Polygon([(tx - 0.5, y), (tx + 0.5, y), (tx, y - 0.7)], fc=color, ec="none", alpha=0.9))
        for i, frac in enumerate((0.8, 0.55)):
            ax.plot([x + 0.5, x + 0.5 + (w - 1) * frac], [y + 1.3 - i * 0.7] * 2,
                    color=BG, lw=5, solid_capstyle="round", alpha=0.55)
    _save(fig, "arpad")


if __name__ == "__main__":
    cover_top100()
    cover_dist()
    cover_titles()
    cover_player()
    cover_games()
    cover_arpad()
    cover_gm_peak()
