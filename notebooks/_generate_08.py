"""Generate notebook 08_qc_elo_analysis.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_08.py
"""

from pathlib import Path
import nbformat as nbf

NBDIR = Path(__file__).resolve().parent


def make_notebook(path: Path, cells: list[tuple[str, str]]):
    nb = nbf.v4.new_notebook()
    nb.cells = [
        (nbf.v4.new_markdown_cell(src) if kind == "md" else nbf.v4.new_code_cell(src))
        for kind, src in cells
    ]
    nb.metadata = {
        "kernelspec": {"display_name": "Python 3 (.venv)", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    }
    with path.open("w") as f:
        nbf.write(nb, f)
    print(f"wrote {path}")


SETUP = """\
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from _setup import load_query, apply_style

import numpy as np
import pandas as pd
from IPython.display import display

apply_style()
"""

LOAD = """\
sql = '''
WITH jan2023 AS (
    -- Letzte verfügbare Rating-Eintrag auf oder vor Jan 2023 pro Spieler
    SELECT DISTINCT ON (fide_id)
        fide_id,
        COALESCE(std_rating, published_rating) AS rating_2023
    FROM rating_history
    WHERE period <= '2023-01-01'
      AND COALESCE(std_rating, published_rating) IS NOT NULL
    ORDER BY fide_id, period DESC
)
SELECT
    q.fide_id,
    p.name,
    COALESCE(j.rating_2023, p.std_rating)               AS rating_2023,
    CASE
        WHEN COALESCE(j.rating_2023, p.std_rating) >= 2400 THEN 'ELO >= 2400'
        WHEN COALESCE(j.rating_2023, p.std_rating) >= 2000 THEN 'ELO 2000-2400'
        ELSE                                                     'ELO < 2000'
    END                                                  AS elo_group,
    TO_CHAR(q.period_start, 'YY-MM')                    AS period,
    q.period_start,
    q.expected_change,
    q.scraped_change,
    q.delta,
    q.flag
FROM qc_rating_check q
JOIN players p USING (fide_id)
LEFT JOIN jan2023 j USING (fide_id)
WHERE q.period_start >= '2015-01-01'
ORDER BY q.fide_id, q.period_start
'''

df = load_query(sql)
df['period_start'] = pd.to_datetime(df['period_start'])
df['abs_delta'] = df['delta'].abs()

# Feste Perioden-Reihenfolge (alle 23 Halbjahreszeiträume ab 2015)
ALL_PERIODS = sorted(df['period'].unique())

print(f"{len(df):,} QC-Fenster geladen, {df['fide_id'].nunique():,} Spieler")
print(f"ELO-Gruppen (Basis Jan 2023):")
print(df.groupby('elo_group')['fide_id'].nunique().rename('Spieler'))
"""

PIVOT_FUNC = """\
BUCKET_BINS   = [-np.inf, 0.001, 2, 5, 10, 20, 50, np.inf]
BUCKET_LABELS = ['=0', '1-2', '3-5', '6-10', '11-20', '21-50', '>50']
OK_CUTOFF = 5   # |delta| <= 5 gilt als ok

def assign_bucket(s):
    abs_s = s.abs()
    return pd.cut(abs_s, bins=BUCKET_BINS, labels=BUCKET_LABELS, right=True)

def pivot_table(sub: pd.DataFrame, title: str) -> pd.DataFrame:
    sub = sub.copy()
    sub['bucket'] = assign_bucket(sub['delta'])

    # Absolute Counts
    counts = (
        sub.groupby(['bucket', 'period'], observed=True)
           .size()
           .unstack('period', fill_value=0)
           .reindex(columns=ALL_PERIODS, fill_value=0)
           .reindex(BUCKET_LABELS)
    )
    # Periode-Totals
    totals = counts.sum(axis=0)

    # Prozentwerte pro Spalte
    pct = counts.div(totals, axis=1).mul(100).round(1)

    # OK-Summe einfügen (nach Zeile '3-5', vor '6-10')
    ok_row = pct.loc[['=0', '1-2', '3-5']].sum(axis=0).round(1).rename('--- ok ≤5 ---')
    pct = pd.concat([pct.iloc[:3], ok_row.to_frame().T, pct.iloc[3:]])

    # N pro Periode als letzte Zeile
    n_row = totals.rename('N (Fenster)')
    pct = pd.concat([pct, n_row.to_frame().T])

    pct.index.name = f'|delta|   {title}'
    return pct

def style_pivot(pct: pd.DataFrame) -> 'pd.io.formats.style.Styler':
    \"\"\"Farbliche Hervorhebung: Zellen im schlechten Bereich rot, im guten grün.\"\"\"
    def color(val, row_label):
        if row_label in ('--- ok ≤5 ---',):
            # Grün-Skala: je höher desto grüner
            try:
                v = float(val)
                intensity = int(min(v, 100) / 100 * 120)
                return f'background-color: rgb({255-intensity},{255},{255-intensity}); color: black'
            except Exception:
                return ''
        if row_label in ('>50', '21-50'):
            try:
                v = float(val)
                if v == 0:
                    return ''
                intensity = int(min(v * 4, 100) / 100 * 150)
                return f'background-color: rgb(255,{255-intensity},{255-intensity}); color: black'
            except Exception:
                return ''
        return ''

    styled = pct.style
    for row_label in pct.index:
        styled = styled.apply(
            lambda col, rl=row_label: [color(v, rl) for v in col],
            subset=pd.IndexSlice[row_label, :],
            axis=1
        )
    styled = styled.format(
        lambda v: f'{v:.1f}' if isinstance(v, float) else str(int(v)),
        na_rep='-'
    )
    styled = styled.set_table_styles([
        {'selector': 'th', 'props': [('font-size', '10px'), ('text-align', 'center')]},
        {'selector': 'td', 'props': [('font-size', '10px'), ('text-align', 'right'), ('padding', '2px 6px')]},
        {'selector': 'tr:nth-child(4)', 'props': [('border-top', '2px solid #555'), ('font-weight', 'bold')]},
    ])
    return styled
"""

TOP10_FUNC = """\
def top10_table(sub: pd.DataFrame, title: str) -> pd.DataFrame:
    \"\"\"Top-10 Spieler mit der grössten kumulativen absoluten ELO-Abweichung.
    Ausgabe: Spieler in Zeilen, Zeiträume in Spalten (delta-Wert pro Zelle).
    Letzte drei Spalten: Σ|Δ|, Ø|Δ|, Max|Δ|.
    \"\"\"
    # Rang nach Σ|Δ|
    ranking = (
        sub.groupby(['fide_id', 'name', 'rating_2023'])['abs_delta']
           .sum()
           .sort_values(ascending=False)
           .head(10)
           .reset_index()
    )
    top_ids = ranking['fide_id'].tolist()

    # Pivot: Spieler × Periode, Werte = delta (mit Vorzeichen)
    top_df = sub[sub['fide_id'].isin(top_ids)].copy()
    pivot = (
        top_df.pivot_table(index='fide_id', columns='period', values='delta',
                           aggfunc='first')
              .reindex(columns=ALL_PERIODS)
    )

    # Summenspalten anhängen
    pivot['Σ|Δ|']  = top_df.groupby('fide_id')['abs_delta'].sum().round(1)
    pivot['Ø|Δ|']  = top_df.groupby('fide_id')['abs_delta'].mean().round(1)
    pivot['Max|Δ|'] = top_df.groupby('fide_id')['abs_delta'].max().round(1)

    # Spieler-Labels als Index (Name + ELO)
    label_map = ranking.set_index('fide_id').apply(
        lambda r: f\"{r['name']} ({int(r['rating_2023']) if pd.notna(r['rating_2023']) else '?'})\", axis=1
    )
    pivot.index = pivot.index.map(label_map)
    pivot = pivot.reindex(ranking['fide_id'].map(label_map))   # Rangordnung

    pivot.index.name = 'Spieler (ELO Jan23)'
    pivot.columns.name = None

    # Styling: positive delta = rot (zu wenig scraped), negativ = blau
    SUMMARY_COLS = ['Σ|Δ|', 'Ø|Δ|', 'Max|Δ|']
    period_cols  = [c for c in pivot.columns if c not in SUMMARY_COLS]

    def color_cell(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return ''
        if np.isnan(f):
            return ''
        if abs(f) <= 5:
            return 'color: #888'
        if f > 0:
            intensity = int(min(abs(f) / 50 * 200, 200))
            return f'background-color: rgb(255,{255-intensity},{255-intensity})'
        else:
            intensity = int(min(abs(f) / 50 * 200, 200))
            return f'background-color: rgb({255-intensity},{255-intensity},255)'

    styled = (
        pivot.style
             .map(color_cell, subset=period_cols)
             .format(lambda v: f'{v:+.1f}' if isinstance(v, float) and not np.isnan(v) else '',
                     subset=period_cols)
             .format('{:.1f}', subset=SUMMARY_COLS, na_rep='-')
             .set_table_styles([
                 {'selector': 'th', 'props': [('font-size', '10px'), ('text-align', 'center')]},
                 {'selector': 'td', 'props': [('font-size', '10px'), ('text-align', 'right'),
                                              ('padding', '2px 5px'), ('min-width', '38px')]},
             ])
    )

    print(f'\\n--- {title}: Top-10 nach Σ|Δ| (delta = erwartet − gescraped, +rot / -blau) ---')
    display(styled)
    return pivot
"""

SECTION_GESAMT = """\
from IPython.display import HTML
print('=' * 70)
print('GESAMT — alle Spieler in qc_rating_check ab 2015')
print('=' * 70)
pct_all = pivot_table(df, 'Gesamt')
display(style_pivot(pct_all))
top10_table(df, 'Gesamt')
"""

SECTION_2400 = """\
print('=' * 70)
print('ELO >= 2400  (gemessen Jan 2023)')
print('=' * 70)
sub = df[df['elo_group'] == 'ELO >= 2400']
print(f'{sub[\"fide_id\"].nunique()} Spieler, {len(sub):,} Fenster')
pct_2400 = pivot_table(sub, 'ELO >= 2400')
display(style_pivot(pct_2400))
top10_table(sub, 'ELO >= 2400')
"""

SECTION_MID = """\
print('=' * 70)
print('ELO 2000–2400  (gemessen Jan 2023)')
print('=' * 70)
sub = df[df['elo_group'] == 'ELO 2000-2400']
print(f'{sub[\"fide_id\"].nunique()} Spieler, {len(sub):,} Fenster')
pct_mid = pivot_table(sub, 'ELO 2000-2400')
display(style_pivot(pct_mid))
top10_table(sub, 'ELO 2000-2400')
"""

SECTION_LOW = """\
print('=' * 70)
print('ELO < 2000  (gemessen Jan 2023)')
print('=' * 70)
sub = df[df['elo_group'] == 'ELO < 2000']
print(f'{sub[\"fide_id\"].nunique()} Spieler, {len(sub):,} Fenster')
pct_low = pivot_table(sub, 'ELO < 2000')
display(style_pivot(pct_low))
top10_table(sub, 'ELO < 2000')
"""

cells = [
    ("md", "# 08 — QC-Analyse: Rating-Delta nach ELO-Klasse und Zeitraum\n\n"
           "Für jeden Spieler werden die QC-Fenster ab Jan 2015 ausgewertet:\n"
           "```\n"
           "delta = expected_change (TXT-Snapshot) − scraped_change (Σ rating_change_weighted)\n"
           "```\n"
           "**ELO-Klassifikation** basiert auf dem Rating vom **Jan 2023** "
           "(vor der FIDE-Korrektur für Spieler unter 2000).\n\n"
           "Jede Pivot-Tabelle zeigt den **Prozentanteil** der Fenster pro Delta-Bucket "
           "für jeden der 23 Halbjahreszeiträume (Feb 2015 → Jan 2026)."),
    ("code", SETUP),
    ("md", "## Datenbasis"),
    ("code", LOAD),
    ("md", "## Hilfsfunktionen"),
    ("code", PIVOT_FUNC),
    ("code", TOP10_FUNC),
    ("md", "---\n## 1 — Gesamt"),
    ("code", SECTION_GESAMT),
    ("md", "---\n## 2 — ELO ≥ 2400"),
    ("code", SECTION_2400),
    ("md", "---\n## 3 — ELO 2000–2400"),
    ("code", SECTION_MID),
    ("md", "---\n## 4 — ELO < 2000"),
    ("code", SECTION_LOW),
]

if __name__ == "__main__":
    make_notebook(NBDIR / "08_qc_elo_analysis.ipynb", cells)
