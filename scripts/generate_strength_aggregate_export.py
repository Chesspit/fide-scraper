"""Generate die aggregierte "Stärke-Bucket-Auswertung" als eigenständige HTML-Seite —
Pendant zu scripts/generate_player_strength_cards.py, aber eine Gesamt-Tabelle statt
einer Karte pro Spielerin.

Liest die von Notebook 16 (Top-40) bzw. Notebook 17 (Top-20) exportierte
`*_strength_summary_overall_aggregate.csv` und rendert daraus exakt die Tabelle,
die zuvor für die Top-40-Auswertung von Hand gebaut wurde (exports/
top40_frauen_staerke_auswertung.html) — hier parametrisiert für beliebige Kohorten.

Nutzung:
    .venv/bin/python scripts/generate_strength_aggregate_export.py \
        --agg-csv notebooks/top20_2400_strength_summary_overall_aggregate.csv \
        --per-player-csv notebooks/top20_2400_strength_summary_overall_per_player.csv \
        --cohort-size 20 \
        --notebook 17_top20_2400_strength_breakdown.ipynb \
        --data-stand 2026-08-11 \
        --out exports/top20_frauen_staerke_auswertung.html
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from generate_strength_methodology import APPENDIX_STYLE, build_methodology_fragment

ROOT = Path(__file__).resolve().parent.parent

BUCKET_ORDER = ["schwächer", "gleich", "stärker", "Gesamt"]
BUCKET_ARROW = {"schwächer": "▽", "gleich": "=", "stärker": "△", "Gesamt": "Σ"}
SEX_ORDER = ["Gesamt", "F", "M"]
SEX_LABEL = {"Gesamt": "Gesamt", "F": "Frauen", "M": "Männer"}
SEX_CLASS = {"Gesamt": "gesamt", "F": "frauen", "M": "maenner"}


def fmt_int(n: int) -> str:
    return format(int(n), ",").replace(",", ".")


def fmt_ratio(x: float) -> str:
    return f"{x:.3f}"


def fmt_delta(x: float) -> str:
    return f"{x:+.3f}"


def render_rows(agg: pd.DataFrame) -> str:
    sub = agg.set_index(["strength_bucket", "sex_group"])
    rows = []
    for bucket in BUCKET_ORDER:
        is_total_bucket = bucket == "Gesamt"
        for i, sex in enumerate(SEX_ORDER):
            row = sub.loc[(bucket, sex)]
            row_classes = " ".join(
                c for c in ["group-first" if i == 0 else "", "group-total" if is_total_bucket else ""] if c
            )
            bucket_cell = (
                f'<span class="bucket-tag"><span class="bucket-arrow">{BUCKET_ARROW[bucket]}</span>{bucket}</span>'
                if i == 0 else ""
            )
            sex_chip = f'<span class="sex-chip {SEX_CLASS[sex]}">{SEX_LABEL[sex]}</span>'
            n = int(row["n_partien"])
            win_pct = row["siege"] / n * 100
            draw_pct = row["remis"] / n * 100
            loss_pct = row["niederlagen"] / n * 100
            pq = row["punktequote"]
            delta = row["mean_delta"]
            delta_cls = "delta-pos" if delta >= 0 else "delta-neg"
            rows.append(
                f'<tr class="{row_classes}"><td class="bucket-cell">{bucket_cell}</td>'
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
  .stat-row { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 0.3rem; }
  .stat { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 0.5rem 0.85rem; min-width: 6.5rem; }
  .stat .n { font-variant-numeric: tabular-nums; font-size: 1.15rem; font-weight: 600; line-height: 1.1; }
  .stat .l { font-size: 0.66rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 0.15rem; }
  .legend-row { display: flex; flex-wrap: wrap; gap: 1.2rem; margin: 1.3rem 0 0.9rem; font-size: 0.78rem; color: var(--muted); align-items: center; }
  .legend-item { display: flex; align-items: center; gap: 0.4rem; }
  .dot { width: 0.65rem; height: 0.65rem; border-radius: 50%; flex: none; }
  .table-wrap { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; overflow: visible; }
  table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 0.76rem; }
  thead th { text-align: right; padding: 0.6rem 0.6rem; border-bottom: 1px solid var(--rule-strong);
    color: var(--muted); font-weight: 600; font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.04em; }
  thead th:first-child, thead th:nth-child(2) { text-align: left; }
  tbody td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--rule); text-align: right;
    font-variant-numeric: tabular-nums; white-space: nowrap; }
  tbody tr.group-first td { border-top: 2px solid var(--rule-strong); }
  tbody tr.group-total td { border-top: 2px solid var(--accent); background: var(--accent-soft); }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr { break-inside: avoid; }
  thead { display: table-header-group; }
  td.bucket-cell { text-align: left; }
  .bucket-tag { display: inline-flex; align-items: center; gap: 0.4rem; font-weight: 700; font-size: 0.82rem; }
  .bucket-arrow { font-size: 0.88rem; opacity: 0.75; }
  td.sex-cell { text-align: left; }
  .sex-chip { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.68rem; font-weight: 700; letter-spacing: 0.02em; }
  .sex-chip.gesamt { background: var(--rule); color: var(--muted); }
  .sex-chip.frauen { background: var(--women-soft); color: var(--women); }
  .sex-chip.maenner { background: var(--men-soft); color: var(--men); }
  .wdl-bar { display: flex; width: 7rem; height: 0.5rem; border-radius: 3px; overflow: hidden;
    margin-left: auto; box-shadow: inset 0 0 0 1px var(--rule-strong); }
  .wdl-bar span { display: block; height: 100%; }
  .wdl-win { background: var(--win); } .wdl-draw { background: var(--draw); } .wdl-loss { background: var(--loss); }
  .wdl-nums { font-size: 0.68rem; color: var(--muted); margin-top: 0.2rem; text-align: right; }
  .pq-cell { min-width: 7rem; }
  .pq-track { position: relative; width: 7rem; height: 0.5rem; background: var(--rule); border-radius: 3px; margin-left: auto; overflow: hidden; }
  .pq-fill { position: absolute; left: 0; top: 0; bottom: 0; background: var(--accent); border-radius: 3px; }
  .pq-mid { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--rule-strong); }
  .pq-num { font-weight: 700; margin-bottom: 0.15rem; }
  .delta-pos { color: var(--win); font-weight: 700; } .delta-neg { color: var(--loss); font-weight: 700; }
  .note { margin-top: 1.3rem; font-size: 0.78rem; color: var(--muted); line-height: 1.55;
    background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 0.85rem 1rem; }
  .note b { color: var(--ink); }
  footer { margin-top: 1.4rem; font-size: 0.72rem; color: var(--faint); line-height: 1.55; }
  footer code { background: var(--accent-soft); color: var(--accent); padding: 0.05rem 0.35rem; border-radius: 4px; font-size: 0.68rem; }
""" + APPENDIX_STYLE


def build_fragment(agg: pd.DataFrame, cohort_size: int, n_players: int,
                    notebook_name: str, source_csv: str, data_stand: str,
                    with_methodology: bool = False) -> str:
    """Content-only fragment: <title> + <style> + markup, no doctype/html/head/body."""
    cohort_label = f"Top-{cohort_size}-Frauen"
    # Nur die "Gesamt"-Bucket-Zeilen heranziehen (Summe über schwächer/gleich/stärker) —
    # sonst würden n_total/n_known doppelt gezählt, weil dieselben Partien jetzt sowohl in
    # ihrem echten Stärke-Bucket als auch im Gesamt-Bucket-Rollup stehen.
    overall = agg[agg.strength_bucket == "Gesamt"]
    n_total = int(overall.loc[overall.sex_group == "Gesamt", "n_partien"].iloc[0])
    n_known = int(overall.loc[overall.sex_group.isin(["F", "M"]), "n_partien"].sum())
    n_unknown = n_total - n_known
    unknown_pct = n_unknown / n_total * 100

    return f"""<title>Stärke-Bucket-Auswertung — {cohort_label} 2400+</title>
<style>{STYLE}</style>
<div class="page">
  <header class="hero">
    <div class="eyebrow">FIDE Rating · {cohort_label} im 2400+-Fenster</div>
    <h1>Ergebnis nach Gegnerstärke &amp; Gegner-Geschlecht</h1>
    <p class="lede">
      Alle Partien der {n_players} Top-{cohort_size}-Spielerinnen ab ihrem individuellen
      <b>Schwellenjahr</b> (erstes Jahr mit Dezember-Elo ≥ 2400) bis 2025, eingeteilt nach
      Gegnerstärke zum Partie-Zeitpunkt (<code>opponent_rating − own_rating</code>, ±50 Elo)
      und nach Gegner-Geschlecht. Aggregiert über alle Spielerinnen und den gesamten Zeitraum.
    </p>
    <div class="stat-row">
      <div class="stat"><div class="n">{fmt_int(n_total)}</div><div class="l">Partien gesamt</div></div>
      <div class="stat"><div class="n">{n_players}</div><div class="l">Spielerinnen</div></div>
      <div class="stat"><div class="n">4 × 3</div><div class="l">Bucket × Geschlecht</div></div>
    </div>
  </header>

  <div class="legend-row">
    <div class="legend-item"><span class="dot" style="background:var(--win)"></span> Sieg</div>
    <div class="legend-item"><span class="dot" style="background:var(--draw)"></span> Remis</div>
    <div class="legend-item"><span class="dot" style="background:var(--loss)"></span> Niederlage</div>
    <div class="legend-item"><span class="dot" style="background:var(--accent)"></span> Punktequote-Balken (Mitte = 0,50)</div>
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
{render_rows(agg)}
      </tbody>
    </table>
  </div>

  <div class="note">
    <b>Lesehinweis:</b> "Gesamt" wird zweifach verwendet — als Gegner-Spalte (Frauen + Männer
    + unbekanntes Geschlecht, {fmt_int(n_unknown)} Partien bzw. ≈{unknown_pct:.1f}&nbsp;% ohne
    bekanntes Gegner-Geschlecht, daher summieren sich die Frauen- und Männer-Zeilen nicht exakt
    zur Gesamt-Spalte) und als vierter <b>Σ&nbsp;Gesamt</b>-Bucket unten (farblich abgesetzt),
    der schwächer + gleich + stärker zu einer Zeile zusammenfasst. Bucket-Grenzen: <b>gleich</b>
    schließt ±50&nbsp;Elo beidseitig ein; <b>schwächer</b>/<b>stärker</b> beginnen erst echt
    darüber/darunter. Ø-Gegner-Elo und Ø-Elo-Δ sind auf ganze bzw. drei Nachkommastellen
    gerundet.
  </div>

  <footer>
    Quelle: Notebook <code>{notebook_name}</code> ·
    Export <code>{source_csv}</code> · Datenstand {data_stand}.
  </footer>
{build_methodology_fragment(cohort_size, n_players) if with_methodology else ""}
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
    ap.add_argument("--agg-csv", required=True, help="*_strength_summary_overall_aggregate.csv")
    ap.add_argument("--per-player-csv", required=True, help="*_strength_summary_overall_per_player.csv (für Spielerinnenzahl)")
    ap.add_argument("--cohort-size", type=int, required=True, help="z.B. 40 oder 20")
    ap.add_argument("--notebook", required=True, help="Quell-Notebook, z.B. 17_top20_2400_strength_breakdown.ipynb")
    ap.add_argument("--data-stand", required=True, help="z.B. 2026-08-11")
    ap.add_argument("--out", required=True, help="Vollständiges HTML-Dokument (für Chrome headless --print-to-pdf)")
    ap.add_argument("--fragment-out", default=None,
                     help="Content-only Fragment (für den Artifact-Publish); Standard: <out>.fragment.html")
    ap.add_argument("--with-methodology", action="store_true",
                     help="Methodik-Seite (siehe generate_strength_methodology.py) anhängen")
    args = ap.parse_args()

    agg = pd.read_csv(args.agg_csv)
    per_player = pd.read_csv(args.per_player_csv)
    n_players = per_player["name"].nunique()

    fragment = build_fragment(
        agg, args.cohort_size, n_players, args.notebook,
        Path(args.agg_csv).name, args.data_stand,
        with_methodology=args.with_methodology,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(wrap_full_document(fragment), encoding="utf-8")
    print(f"wrote {out_path}")

    fragment_path = Path(args.fragment_out) if args.fragment_out else out_path.with_suffix(".fragment.html")
    fragment_path.write_text(fragment, encoding="utf-8")
    print(f"wrote {fragment_path}")


if __name__ == "__main__":
    main()
