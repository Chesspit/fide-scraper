"""Generate die kumulative Top-N-Frauen-Roster-Liste als eigenständige HTML-Seite.

Liest die von scripts/export_female_roster.py erzeugte Roster-CSV
(fide_id, name, year_added, 2016..2025) und rendert daraus die Tabelle im
Format des bestehenden Top-40-Exports (exports/top40_frauen_liste.html) —
hier parametrisiert für beliebige Kohortengröße.

Nutzung:
    .venv/bin/python scripts/generate_roster_list_export.py \
        --roster-csv notebooks/top20_female_roster_2016-2025.csv \
        --cohort-size 20 \
        --data-stand 2026-08-11 \
        --out exports/top20_frauen_liste.html
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

YEARS = list(range(2016, 2026))

STYLE = """
  @page { size: A4 landscape; margin: 12mm 10mm; }
  :root {
    --bg: #f1efe4; --surface: #fdfcf7; --ink: #23261f; --muted: #656a5c; --faint: #9a9d8e;
    --accent: #3f6b52; --accent-soft: #e4ecdf; --accent-warm: #a15f34; --accent-warm-soft: #f2e3d4;
    --rule: #ddd8c5; --rule-strong: #c7c1a9; --shadow: 0 1px 2px rgba(35,38,31,0.06);
  }
  :root[data-theme="dark"] {
    --bg: #14160f; --surface: #1b1e17; --ink: #e9e6d8; --muted: #a3a794; --faint: #6e7263;
    --accent: #74b691; --accent-soft: #223326; --accent-warm: #dc9f6c; --accent-warm-soft: #332619;
    --rule: #2c2f25; --rule-strong: #3c4033; --shadow: 0 1px 2px rgba(0,0,0,0.3);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #14160f; --surface: #1b1e17; --ink: #e9e6d8; --muted: #a3a794; --faint: #6e7263;
      --accent: #74b691; --accent-soft: #223326; --accent-warm: #dc9f6c; --accent-warm-soft: #332619;
      --rule: #2c2f25; --rule-strong: #3c4033; --shadow: 0 1px 2px rgba(0,0,0,0.3);
    }
  }
  * { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }
  .page { max-width: 100%; margin: 0 auto; padding: 0.5rem 0.5rem 2rem; }
  header.hero { display: flex; flex-direction: column; gap: 0.9rem; margin-bottom: 2rem;
    border-bottom: 1px solid var(--rule); padding-bottom: 1.4rem; }
  .eyebrow { font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); font-weight: 600; }
  h1 { font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif; font-weight: 500;
    font-size: 1.9rem; line-height: 1.15; margin: 0; letter-spacing: -0.01em; }
  .lede { max-width: 78ch; color: var(--muted); font-size: 0.92rem; line-height: 1.5; margin: 0; }
  .lede b { color: var(--ink); font-weight: 600; }
  .stat-row { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 0.4rem; }
  .stat { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 0.5rem 0.85rem; min-width: 7rem; }
  .stat .n { font-variant-numeric: tabular-nums; font-size: 1.15rem; font-weight: 600; line-height: 1.1; }
  .stat .l { font-size: 0.68rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 0.15rem; }
  .method { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 0.9rem 1.1rem;
    margin-bottom: 1.2rem; font-size: 0.82rem; line-height: 1.55; color: var(--muted); }
  .method b { color: var(--ink); }
  .method .legend { display: flex; flex-wrap: wrap; gap: 1.1rem; margin-top: 0.6rem; padding-top: 0.6rem;
    border-top: 1px dashed var(--rule); font-size: 0.78rem; align-items: center; }
  .legend-item { display: flex; align-items: center; gap: 0.4rem; }
  .swatch { width: 0.8rem; height: 0.8rem; border-radius: 3px; flex: none; }
  .swatch.entry-mark { background: var(--accent-warm-soft); border: 1.5px solid var(--accent-warm); }
  .swatch.badge-mark { background: var(--accent-soft); border: 1px solid var(--accent); }
  .table-wrap { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; overflow: visible; }
  table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 0.78rem; }
  thead th { background: var(--surface); border-bottom: 1px solid var(--rule-strong); padding: 0.55rem 0.5rem;
    text-align: right; font-weight: 600; color: var(--muted); font-size: 0.72rem; letter-spacing: 0.02em; }
  thead th.name-head, thead th.year-head { text-align: left; }
  tbody td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--rule); text-align: right;
    font-variant-numeric: tabular-nums; white-space: nowrap; }
  td.name-cell { background: var(--surface); text-align: left; min-width: 10rem; border-right: 1px solid var(--rule); }
  td.name-cell .name { display: block; font-weight: 600; font-size: 0.8rem; }
  td.name-cell .fid { display: block; font-size: 0.66rem; color: var(--faint); font-variant-numeric: tabular-nums; }
  td.year-cell { background: var(--surface); text-align: center; border-right: 1px solid var(--rule-strong); min-width: 3.4rem; }
  .year-badge { display: inline-block; padding: 0.12rem 0.45rem; border-radius: 999px; font-size: 0.68rem; font-weight: 600;
    background: var(--accent-soft); color: var(--accent); border: 1px solid var(--accent); }
  td.cell.entry { background: var(--accent-warm-soft); color: var(--accent-warm); font-weight: 700; border-radius: 4px; }
  td.cell.empty { color: var(--faint); text-align: center; }
  tr.inactive-row td { opacity: 0.55; }
  tr.inactive-row td.name-cell { opacity: 1; }
  tr.inactive-row td.name-cell .name { color: var(--muted); text-decoration: line-through; text-decoration-color: var(--faint); }
  .inactive-tag { display: inline-block; margin-left: 0.4rem; padding: 0.06rem 0.4rem; border-radius: 999px;
    font-size: 0.62rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;
    background: var(--accent-warm-soft); color: var(--accent-warm); border: 1px solid var(--accent-warm); text-decoration: none; }
  .swatch.inactive-mark { background: transparent; border: 1.5px dashed var(--faint); }
  tbody tr { break-inside: avoid; }
  thead { display: table-header-group; }
  footer { margin-top: 1.4rem; font-size: 0.72rem; color: var(--faint); line-height: 1.55; }
  footer code { background: var(--accent-soft); color: var(--accent); padding: 0.05rem 0.35rem; border-radius: 4px; font-size: 0.68rem; }
"""


def render_rows(roster: pd.DataFrame, has_active_col: bool) -> str:
    rows = []
    for _, r in roster.iterrows():
        year_added = int(r["year_added"])
        is_inactive = has_active_col and not bool(r["active"])
        cells = []
        for y in YEARS:
            val = r[str(y)]
            cls = "cell entry" if y == year_added else "cell"
            if pd.isna(val):
                cells.append('<td class="cell empty">–</td>')
            else:
                cells.append(f'<td class="{cls}">{int(val)}</td>')
        tr_cls = ' class="inactive-row"' if is_inactive else ""
        tag = '<span class="inactive-tag">inaktiv</span>' if is_inactive else ""
        rows.append(
            f'<tr data-cohort="{year_added}"{tr_cls}>'
            f'<td class="name-cell"><span class="name">{r["name"]}{tag}</span>'
            f'<span class="fid">{int(r["fide_id"])}</span></td>'
            f'<td class="year-cell"><span class="year-badge y{year_added}">{year_added}</span></td>'
            + "".join(cells) + "</tr>"
        )
    return "\n".join(rows)


def build_fragment(roster: pd.DataFrame, cohort_size: int, data_stand: str) -> str:
    has_active_col = "active" in roster.columns
    n_total = len(roster)
    n_core = int((roster["year_added"] == YEARS[0]).sum())
    n_new = n_total - n_core
    cutoff_note = "über die Jahre unterschiedlich" if cohort_size != 40 else "über die Jahre bei ca. 2440–2460"

    year_headers = "".join(f"<th>{y}</th>" for y in YEARS)

    if has_active_col:
        n_inactive = int((~roster["active"]).sum())
        n_active_listed = n_total - n_inactive
        title = f"Top-{cohort_size}-Frauen 2016–2025 (inkl. inaktive)"
        h1 = f"Top&nbsp;{cohort_size} der Frauen, Jahresende 2016–2025 — inkl. inaktive"
        lede = f"""
      Erweiterte Aufstellung: zusätzlich zur regulären <b>Top&nbsp;{cohort_size}-Liste</b> (aktive Spielerinnen)
      sind hier auch Spielerinnen aufgeführt, die zu einem Jahresende historisch in den
      Top&nbsp;{cohort_size} standen, inzwischen aber <b>inaktiv</b> sind (FIDE-Status) und deshalb in der
      regulären Liste <b>nicht berücksichtigt</b> werden — durchgestrichener Name, abgeblendete
      Zeile, Markierung „inaktiv". Aufnahmejahr und Elo-Werte sind für diese Zeilen trotzdem
      die historisch tatsächlichen. Jede Zeile zeigt den <b>Elo-Wert am Jahresende</b> für alle
      zehn Jahre.
    """
        stat_extra = (
            f'<div class="stat"><div class="n">{n_active_listed}</div>'
            f'<div class="l">davon regulär (aktiv)</div></div>'
            f'<div class="stat"><div class="n">{n_inactive}</div>'
            f'<div class="l">davon inaktiv, ausgeschlossen</div></div>'
        )
        method = f"""
    Quelle: <b>rating_history.published_rating</b> (FIDE-TXT-Snapshots), Top&nbsp;{cohort_size} je Periode
    <code>YYYY-12-01</code>, nur <b>players.sex = 'F'</b>. Rang wird über alle Spielerinnen
    (aktiv + inaktiv) je Periode berechnet — das entspricht der historisch tatsächlichen
    Platzierung; erst danach wird der aktuelle <b>active</b>-Status als Referenz eingeblendet.
    Aufnahmejahr = erstes Jahr, in dem eine Spielerin in den Top&nbsp;{cohort_size} stand.
    <div class="legend">
      <div class="legend-item"><span class="swatch badge-mark"></span> Aufnahmejahr (Badge)</div>
      <div class="legend-item"><span class="swatch entry-mark"></span> Elo im Aufnahmejahr (hervorgehoben)</div>
      <div class="legend-item"><span class="swatch inactive-mark"></span> inaktiv — nicht in der regulären Liste</div>
      <div class="legend-item">– = kein Rating in diesem Jahr</div>
    </div>
  """
    else:
        title = f"Top-{cohort_size}-Frauen 2016–2025"
        h1 = f"Top&nbsp;{cohort_size} der Frauen, Jahresende 2016–2025"
        lede = f"""
      Kumulative Aufstellung: Basis ist die <b>Top&nbsp;{cohort_size} nach Elo (Dezember 2016)</b>,
      bereinigt um inzwischen <b>inaktive</b> Spielerinnen. Für jedes Folgejahr (2017–2025)
      wird geprüft, welche <b>aktiven</b> Spielerinnen neu in die Top&nbsp;{cohort_size} aufgerückt sind —
      diese werden der Liste ergänzt, niemand wird wieder entfernt. Jede Zeile zeigt den
      <b>Elo-Wert am Jahresende</b> für alle zehn Jahre.
    """
        stat_extra = ""
        method = f"""
    Quelle: <b>rating_history.published_rating</b> (FIDE-TXT-Snapshots), Top&nbsp;{cohort_size} je Periode
    <code>YYYY-12-01</code>, nur <b>players.sex = 'F'</b> und aktuell <b>active = TRUE</b>.
    Aufnahmejahr = erstes Jahr, in dem eine Spielerin aktiv in den Top&nbsp;{cohort_size} stand.
    <div class="legend">
      <div class="legend-item"><span class="swatch badge-mark"></span> Aufnahmejahr (Badge)</div>
      <div class="legend-item"><span class="swatch entry-mark"></span> Elo im Aufnahmejahr (hervorgehoben)</div>
      <div class="legend-item">– = kein Rating in diesem Jahr</div>
    </div>
  """

    return f"""<title>{title}</title>
<style>{STYLE}</style>
<div class="page">
  <header class="hero">
    <div class="eyebrow">FIDE Rating · Frauen-Kohorte</div>
    <h1>{h1}</h1>
    <p class="lede">{lede}</p>
    <div class="stat-row">
      <div class="stat"><div class="n">{n_total}</div><div class="l">Spielerinnen gesamt</div></div>
      <div class="stat"><div class="n">{n_core}</div><div class="l">Kern 2016</div></div>
      <div class="stat"><div class="n">{n_new}</div><div class="l">neu 2017–2025</div></div>
      {stat_extra}
      <div class="stat"><div class="n">2016–25</div><div class="l">Zeitfenster</div></div>
    </div>
  </header>

  <div class="method">{method}</div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="name-head">Spielerin</th>
          <th class="year-head">Aufn.</th>
          {year_headers}
        </tr>
      </thead>
      <tbody>
{render_rows(roster, has_active_col)}
      </tbody>
    </table>
  </div>

  <footer>
    Datenstand: DB-Snapshot vom {data_stand} · Elo = <code>published_rating</code> zum Periodenende Dezember ·
    Cutoff-Rating der Top&nbsp;{cohort_size} lag {cutoff_note}.
  </footer>
</div>
"""


def wrap_full_document(fragment: str) -> str:
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
    ap.add_argument("--roster-csv", required=True)
    ap.add_argument("--cohort-size", type=int, required=True)
    ap.add_argument("--data-stand", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fragment-out", default=None)
    args = ap.parse_args()

    roster = pd.read_csv(args.roster_csv)
    fragment = build_fragment(roster, args.cohort_size, args.data_stand)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(wrap_full_document(fragment), encoding="utf-8")
    print(f"wrote {out_path}")

    fragment_path = Path(args.fragment_out) if args.fragment_out else out_path.with_suffix(".fragment.html")
    fragment_path.write_text(fragment, encoding="utf-8")
    print(f"wrote {fragment_path}")


if __name__ == "__main__":
    main()
