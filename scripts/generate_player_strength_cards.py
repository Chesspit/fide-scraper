"""Generate Einzelspielerinnen-Karten (Stärke-Bucket x Gegner-Geschlecht) als
eigenständige HTML-Seite — Pendant zur aggregierten "Stärke-Bucket-Auswertung"
Artefakt-Seite, aber eine Karte pro Spielerin statt einer Gesamt-Tabelle.

Quelle: notebooks/top40_2400_strength_summary_overall_per_player.csv
        (erzeugt von notebooks/16_top40_2400_strength_breakdown.ipynb)
        notebooks/top40_female_roster_2016-2025.csv
        (erzeugt von notebooks/14_top40_female_vs_band_men.ipynb — für FIDE-ID
        und Jahresend-Elo 2016-2025, daraus wird das Schwellenjahr abgeleitet)

Nutzung:
    .venv/bin/python scripts/generate_player_strength_cards.py \
        --players "Hou, Yifan" "Lei, Tingjie" "Ju, Wenjun" \
        --out exports/top40_frauen_staerke_einzelspielerinnen.html

Ohne --players: alle Spielerinnen aus dem Roster (Reihenfolge wie in der
Top-40-Liste), --limit N für die ersten N.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
NBDIR = ROOT / "notebooks"

BUCKET_ORDER = ["schwächer", "gleich", "stärker"]
BUCKET_ARROW = {"schwächer": "▽", "gleich": "=", "stärker": "△"}
SEX_ORDER = ["Gesamt", "F", "M"]
SEX_LABEL = {"Gesamt": "Gesamt", "F": "Frauen", "M": "Männer"}
SEX_CLASS = {"Gesamt": "gesamt", "F": "frauen", "M": "maenner"}
YEAR_COLS = [str(y) for y in range(2016, 2026)]


def fmt_int(n: int) -> str:
    return format(int(n), ",").replace(",", ".")


def fmt_ratio(x: float) -> str:
    return f"{x:.3f}"


def fmt_delta(x: float) -> str:
    return f"{x:+.3f}"


def load_data():
    strength = pd.read_csv(NBDIR / "top40_2400_strength_summary_overall_per_player.csv")
    roster = pd.read_csv(NBDIR / "top40_female_roster_2016-2025.csv")
    roster["threshold_year"] = roster.apply(
        lambda r: next(y for y in range(2016, 2026) if pd.notna(r[str(y)]) and r[str(y)] >= 2400),
        axis=1,
    )
    return strength, roster


def player_stat_row(strength: pd.DataFrame, name: str) -> dict:
    sub = strength[(strength["name"] == name) & (strength["sex_group"] == "Gesamt")]
    return {
        "n_total": int(sub["n_partien"].sum()),
    }


def render_table_rows(strength: pd.DataFrame, name: str) -> str:
    sub = strength[strength["name"] == name].set_index(["strength_bucket", "sex_group"])
    rows = []
    for bucket in BUCKET_ORDER:
        for i, sex in enumerate(SEX_ORDER):
            try:
                row = sub.loc[(bucket, sex)]
            except KeyError:
                row = None
            group_first = " group-first" if i == 0 else ""
            bucket_cell = (
                f'<span class="bucket-tag"><span class="bucket-arrow">{BUCKET_ARROW[bucket]}</span>{bucket}</span>'
                if i == 0 else ""
            )
            sex_chip = f'<span class="sex-chip {SEX_CLASS[sex]}">{SEX_LABEL[sex]}</span>'
            n = 0 if row is None else int(row["n_partien"])
            if row is None or n == 0:
                rows.append(
                    f'<tr class="{group_first.strip()}"><td class="bucket-cell">{bucket_cell}</td>'
                    f'<td class="sex-cell">{sex_chip}</td><td>0</td>'
                    f'<td><span class="no-games">keine Partien</span></td>'
                    f'<td class="pq-cell">–</td><td>–</td><td>–</td></tr>'
                )
                continue
            win_pct = row["siege"] / n * 100
            draw_pct = row["remis"] / n * 100
            loss_pct = row["niederlagen"] / n * 100
            pq = row["punktequote"]
            delta = row["mean_delta"]
            delta_cls = "delta-pos" if delta >= 0 else "delta-neg"
            rows.append(
                f'<tr class="{group_first.strip()}"><td class="bucket-cell">{bucket_cell}</td>'
                f'<td class="sex-cell">{sex_chip}</td>'
                f'<td>{fmt_int(n)}</td>'
                f'<td><div class="wdl-bar"><span class="wdl-win" style="width:{win_pct:.2f}%"></span>'
                f'<span class="wdl-draw" style="width:{draw_pct:.2f}%"></span>'
                f'<span class="wdl-loss" style="width:{loss_pct:.2f}%"></span></div>'
                f'<div class="wdl-nums">{fmt_int(int(row["siege"]))} / {fmt_int(int(row["remis"]))} / {fmt_int(int(row["niederlagen"]))}</div></td>'
                f'<td class="pq-cell"><div class="pq-num">{fmt_ratio(pq)}</div>'
                f'<div class="pq-track"><span class="pq-mid"></span><div class="pq-fill" style="width:{pq*100:.1f}%"></div></div></td>'
                f'<td>{fmt_int(round(row["avg_opponent_rating"]))}</td>'
                f'<td class="{delta_cls}">{fmt_delta(delta)}</td></tr>'
            )
    return "\n".join(rows)


def render_card(strength: pd.DataFrame, roster: pd.DataFrame, name: str, page_break: bool) -> str:
    r = roster[roster["name"] == name].iloc[0]
    stats = player_stat_row(strength, name)
    break_cls = " page-break" if page_break else ""
    return f"""
  <section class="player-card{break_cls}">
    <div class="player-head">
      <div class="player-id">
        <span class="player-name">{name}</span>
        <span class="player-fid">FIDE-ID {int(r['fide_id'])}</span>
      </div>
      <div class="stat-row">
        <div class="stat"><div class="n">{int(r['threshold_year'])}</div><div class="l">Schwellenjahr (Elo≥2400)</div></div>
        <div class="stat"><div class="n">{fmt_int(stats['n_total'])}</div><div class="l">Partien seit Schwelle</div></div>
        <div class="stat"><div class="n">{int(r['year_added'])}</div><div class="l">Top-40-Aufnahme</div></div>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Bucket</th><th>Gegner</th><th>Partien</th>
            <th>Sieg / Remis / Niederlage</th><th>Punktequote</th>
            <th>Ø-Gegner-Elo</th><th>Ø-Elo-Δ</th>
          </tr>
        </thead>
        <tbody>
{render_table_rows(strength, name)}
        </tbody>
      </table>
    </div>
  </section>"""


STYLE = """
  @page { size: A4 portrait; margin: 14mm 12mm; }
  :root {
    --bg: #f1efe4; --surface: #fdfcf7; --ink: #23261f; --muted: #656a5c; --faint: #9a9d8e;
    --rule: #ddd8c5; --rule-strong: #c7c1a9; --accent: #3f6b52; --accent-soft: #e4ecdf;
    --women: #a15f34; --women-soft: #f2e3d4; --men: #375f7a; --men-soft: #e2ebef;
    --win: #2f7d4f; --draw: #b9b39a; --loss: #b0473f;
  }
  :root[data-theme="dark"] {
    --bg: #14160f; --surface: #1b1e17; --ink: #e9e6d8; --muted: #a3a794; --faint: #6e7263;
    --rule: #2c2f25; --rule-strong: #3c4033; --accent: #74b691; --accent-soft: #223326;
    --women: #dc9f6c; --women-soft: #332619; --men: #7fb2d1; --men-soft: #1d2a32;
    --win: #5cb885; --draw: #7c7a68; --loss: #d97a72;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #14160f; --surface: #1b1e17; --ink: #e9e6d8; --muted: #a3a794; --faint: #6e7263;
      --rule: #2c2f25; --rule-strong: #3c4033; --accent: #74b691; --accent-soft: #223326;
      --women: #dc9f6c; --women-soft: #332619; --men: #7fb2d1; --men-soft: #1d2a32;
      --win: #5cb885; --draw: #7c7a68; --loss: #d97a72;
    }
  }
  * { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }
  .page { max-width: 100%; margin: 0 auto; padding: 0.5rem 0.5rem 2rem; }
  header.hero { display: flex; flex-direction: column; gap: 0.9rem; margin-bottom: 1.6rem;
    border-bottom: 1px solid var(--rule); padding-bottom: 1.4rem; }
  .eyebrow { font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); font-weight: 600; }
  h1 { font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif; font-weight: 500;
    font-size: 1.7rem; line-height: 1.18; margin: 0; letter-spacing: -0.01em; }
  .lede { max-width: 78ch; color: var(--muted); font-size: 0.9rem; line-height: 1.5; margin: 0; }
  .lede b { color: var(--ink); font-weight: 600; }
  .lede code { background: var(--accent-soft); color: var(--accent); padding: 0.05rem 0.35rem; border-radius: 4px; font-size: 0.85em; }
  .legend-row { display: flex; flex-wrap: wrap; gap: 1.2rem; margin: 1.1rem 0 0; font-size: 0.78rem; color: var(--muted); align-items: center; }
  .legend-item { display: flex; align-items: center; gap: 0.4rem; }
  .dot { width: 0.65rem; height: 0.65rem; border-radius: 50%; flex: none; }

  .player-card { margin-top: 1.8rem; }
  .player-card.page-break { break-before: page; page-break-before: always; padding-top: 0.3rem; }
  .player-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
    gap: 0.8rem; margin-bottom: 0.9rem; }
  .player-id { display: flex; flex-direction: column; gap: 0.15rem; }
  .player-name { font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif; font-size: 1.3rem; font-weight: 600; }
  .player-fid { font-size: 0.72rem; color: var(--faint); font-variant-numeric: tabular-nums; }
  .stat-row { display: flex; flex-wrap: wrap; gap: 0.6rem; }
  .stat { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 0.4rem 0.75rem; min-width: 6.5rem; }
  .stat .n { font-variant-numeric: tabular-nums; font-size: 1.05rem; font-weight: 600; line-height: 1.1; }
  .stat .l { font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 0.12rem; }

  .table-wrap { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; overflow: visible; }
  table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 0.76rem; }
  thead th { text-align: right; padding: 0.55rem 0.6rem; border-bottom: 1px solid var(--rule-strong);
    color: var(--muted); font-weight: 600; font-size: 0.64rem; text-transform: uppercase; letter-spacing: 0.04em; }
  thead th:first-child, thead th:nth-child(2) { text-align: left; }
  tbody td { padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--rule); text-align: right;
    font-variant-numeric: tabular-nums; white-space: nowrap; }
  tbody tr.group-first td { border-top: 2px solid var(--rule-strong); }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr { break-inside: avoid; }
  thead { display: table-header-group; }
  td.bucket-cell { text-align: left; }
  .bucket-tag { display: inline-flex; align-items: center; gap: 0.4rem; font-weight: 700; font-size: 0.8rem; }
  .bucket-arrow { font-size: 0.86rem; opacity: 0.75; }
  td.sex-cell { text-align: left; }
  .sex-chip { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.66rem; font-weight: 700; letter-spacing: 0.02em; }
  .sex-chip.gesamt { background: var(--rule); color: var(--muted); }
  .sex-chip.frauen { background: var(--women-soft); color: var(--women); }
  .sex-chip.maenner { background: var(--men-soft); color: var(--men); }
  .wdl-bar { display: flex; width: 6.5rem; height: 0.48rem; border-radius: 3px; overflow: hidden;
    margin-left: auto; box-shadow: inset 0 0 0 1px var(--rule-strong); }
  .wdl-bar span { display: block; height: 100%; }
  .wdl-win { background: var(--win); } .wdl-draw { background: var(--draw); } .wdl-loss { background: var(--loss); }
  .wdl-nums { font-size: 0.65rem; color: var(--muted); margin-top: 0.18rem; text-align: right; }
  .no-games { font-size: 0.72rem; color: var(--faint); font-style: italic; }
  .pq-cell { min-width: 6.5rem; }
  .pq-track { position: relative; width: 6.5rem; height: 0.48rem; background: var(--rule); border-radius: 3px; margin-left: auto; overflow: hidden; }
  .pq-fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--accent); border-radius: 3px; }
  .pq-mid { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--rule-strong); }
  .pq-num { font-weight: 700; margin-bottom: 0.12rem; }
  .delta-pos { color: var(--win); font-weight: 700; } .delta-neg { color: var(--loss); font-weight: 700; }
  .note { margin-top: 1.6rem; font-size: 0.78rem; color: var(--muted); line-height: 1.55;
    background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 0.85rem 1rem; }
  .note b { color: var(--ink); }
  footer { margin-top: 1.4rem; font-size: 0.72rem; color: var(--faint); line-height: 1.55; }
  footer code { background: var(--accent-soft); color: var(--accent); padding: 0.05rem 0.35rem; border-radius: 4px; font-size: 0.68rem; }
"""


def build_fragment(strength: pd.DataFrame, roster: pd.DataFrame, players: list[str]) -> str:
    """Content-only fragment: <title> + <style> + markup, no doctype/html/head/body.

    This is what the Artifact tool expects (it supplies its own skeleton).
    """
    cards = "\n".join(
        render_card(strength, roster, name, page_break=(i > 0))
        for i, name in enumerate(players)
    )
    return f"""<title>Stärke-Auswertung je Spielerin — Top-40-Frauen 2400+</title>
<style>{STYLE}</style>
<div class="page">
  <header class="hero">
    <div class="eyebrow">FIDE Rating · Top-40-Frauen im 2400+-Fenster · Einzelauswertung</div>
    <h1>Stärke-Auswertung je Spielerin</h1>
    <p class="lede">
      Pendant zur aggregierten Stärke-Bucket-Auswertung, hier je Spielerin einzeln: Partien ab
      ihrem individuellen <b>Schwellenjahr</b> (erstes Jahr mit Dezember-Elo ≥ 2400) bis 2025,
      eingeteilt nach Gegnerstärke zum Partie-Zeitpunkt (<code>opponent_rating − own_rating</code>,
      ±50 Elo) und nach Gegner-Geschlecht.
    </p>
    <div class="legend-row">
      <div class="legend-item"><span class="dot" style="background:var(--win)"></span> Sieg</div>
      <div class="legend-item"><span class="dot" style="background:var(--draw)"></span> Remis</div>
      <div class="legend-item"><span class="dot" style="background:var(--loss)"></span> Niederlage</div>
      <div class="legend-item"><span class="dot" style="background:var(--accent)"></span> Punktequote-Balken (Mitte = 0,50)</div>
    </div>
  </header>
{cards}
  <div class="note">
    <b>Lesehinweis:</b> "Gesamt" je Bucket enthält auch Partien mit unbekanntem Gegner-Geschlecht —
    daher summieren sich Frauen- und Männer-Zeile nicht immer exakt zur Gesamt-Zeile.
    Bucket-Grenzen: <b>gleich</b> schließt ±50&nbsp;Elo beidseitig ein; <b>schwächer</b>/<b>stärker</b>
    beginnen erst echt darüber/darunter. "keine Partien" = für diese Kombination liegen 0 Partien vor.
  </div>
  <footer>
    Quelle: Notebook <code>16_top40_2400_strength_breakdown.ipynb</code> ·
    Export <code>top40_2400_strength_summary_overall_per_player.csv</code> · Datenstand 2026-08-04.
  </footer>
</div>
"""


def wrap_full_document(fragment: str) -> str:
    """Full standalone document (doctype/html/head/body) for headless-Chrome print-to-pdf."""
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf8">
</head>
<body>
{fragment}
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", nargs="*", default=None, help="Spielerinnen-Namen, z.B. \"Hou, Yifan\"")
    ap.add_argument("--limit", type=int, default=None, help="Nur die ersten N Spielerinnen aus dem Roster")
    ap.add_argument("--out", default=str(ROOT / "exports" / "top40_frauen_staerke_einzelspielerinnen.html"),
                     help="Vollständiges HTML-Dokument (für Chrome headless --print-to-pdf)")
    ap.add_argument("--fragment-out", default=None,
                     help="Content-only Fragment (für den Artifact-Publish); Standard: <out>.fragment.html")
    args = ap.parse_args()

    strength, roster = load_data()

    if args.players:
        players = args.players
    else:
        players = roster["name"].tolist()
        if args.limit:
            players = players[: args.limit]

    missing = [p for p in players if p not in set(roster["name"])]
    if missing:
        raise SystemExit(f"Unbekannte Spielerinnen: {missing}")

    fragment = build_fragment(strength, roster, players)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(wrap_full_document(fragment), encoding="utf-8")
    print(f"wrote {out_path} ({len(players)} Spielerinnen)")

    fragment_path = Path(args.fragment_out) if args.fragment_out else out_path.with_suffix(".fragment.html")
    fragment_path.write_text(fragment, encoding="utf-8")
    print(f"wrote {fragment_path}")


if __name__ == "__main__":
    main()
