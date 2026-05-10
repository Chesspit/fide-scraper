#!/usr/bin/env python3
"""Generate notebooks/11_qc_2008_detail.ipynb

Per player with 2008 QC warn/error:
  Row 1 — Published ELO:   Okt-07, Jan-08, Apr-08, Jul-08, Okt-08, Jan-09
  Row 2 — Partien-Δ:       Apr-08, Jul-08, Okt-08, Jan-09 (Okt-07/Jan-08 leer)
  Row 3 — Unerklärtes Δ:   pro Fenster (published_Δ − game_Δ)
  Row 4 — Kumulativ:       Laufende Summe; Okt-07+Σ game_Δ vs. Okt-08

Jahresprüfsumme: Okt-07 → Okt-08
"""
import nbformat as nbf

CELLS = []


def code(src):
    CELLS.append(nbf.v4.new_code_cell(src.strip()))


def md(src):
    CELLS.append(nbf.v4.new_markdown_cell(src.strip()))


md("""# Notebook 11 — QC 2008: ELO-Detail pro Spieler

Alle Spieler mit QC-Flag `warn` oder `error` in einem 2008-Fenster.

**Verfügbare Daten:**
| Typ | Monate |
|---|---|
| TXT-Snapshots (published_rating) | Okt-07, Jan-08, Apr-08, Jul-08, Okt-08, Jan-09 |
| Partiendaten (Scraping) | Apr-08, Jul-08, Okt-08 |

**Jahresprüfsumme:** Okt-07 → Okt-08
""")

code("""
import os
import warnings
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from IPython.display import display, HTML

warnings.filterwarnings('ignore')
load_dotenv('../.env.notebook')
conn = psycopg2.connect(os.getenv('DATABASE_URL'))

# Snapshot- und Partien-Monate für 2008
SNAP_MONTHS  = ['2007-10-01', '2008-01-01', '2008-04-01',
                '2008-07-01', '2008-10-01', '2009-01-01']
GAME_MONTHS  = ['2008-04-01', '2008-07-01', '2008-10-01', '2009-01-01']
YEAR_START   = '2007-10-01'
YEAR_END     = '2008-10-01'
""")

code("""
# Players with 2008 warn/error
df_players = pd.read_sql(\"\"\"
    SELECT DISTINCT q.fide_id, p.name, p.analysis_group
    FROM qc_rating_check q
    JOIN players p USING (fide_id)
    WHERE EXTRACT(YEAR FROM q.period_end) = 2008
      AND q.flag IN ('warn', 'error')
    ORDER BY p.analysis_group, p.name
\"\"\", conn)

print(f"Spieler mit 2008-Abweichungen: {len(df_players)}")
print(df_players.groupby('analysis_group')['fide_id'].count().to_string())
""")

code("""
ids_sql = ','.join(str(i) for i in df_players['fide_id'].tolist())

# Published ratings für Snapshot-Monate
df_pub = pd.read_sql(f\"\"\"
    SELECT fide_id, period, published_rating
    FROM rating_history
    WHERE period IN ('2007-10-01','2008-01-01','2008-04-01',
                     '2008-07-01','2008-10-01','2009-01-01')
      AND fide_id IN ({ids_sql})
      AND published_rating IS NOT NULL
\"\"\", conn)
df_pub['period'] = pd.to_datetime(df_pub['period'])

# Partien-Δ für Scraping-Monate
df_games = pd.read_sql(f\"\"\"
    SELECT fide_id, period, ROUND(SUM(rating_change_weighted)::numeric, 1) AS game_delta
    FROM game_results
    WHERE period IN ('2008-04-01','2008-07-01','2008-10-01','2009-01-01')
      AND fide_id IN ({ids_sql})
    GROUP BY fide_id, period
\"\"\", conn)
df_games['period'] = pd.to_datetime(df_games['period'])

print(f"Published-Rating-Einträge: {len(df_pub)}")
print(f"Partien-Δ-Einträge:        {len(df_games)}")
""")

code("""
snap_dates  = pd.to_datetime(SNAP_MONTHS)
game_dates  = pd.to_datetime(GAME_MONTHS)
col_labels  = [d.strftime('%b-%y') for d in snap_dates]
game_labels = [d.strftime('%b-%y') for d in game_dates]


def build_player_table(fide_id):
    # --- Row 1: Published ELO ---
    pub = (df_pub[df_pub.fide_id == fide_id]
           .set_index('period')['published_rating']
           .reindex(snap_dates))
    row_pub = pd.Series(pub.values, index=col_labels,
                        name='Publiziert ELO', dtype='Float64')

    # --- Row 2: Partien-Δ (nur Apr/Jul/Okt-08 und Jan-09) ---
    games = (df_games[df_games.fide_id == fide_id]
             .set_index('period')['game_delta']
             .reindex(game_dates))
    # Erste zwei Spalten (Okt-07, Jan-08) leer lassen
    game_vals = [np.nan, np.nan] + list(games.values)
    row_game = pd.Series(game_vals, index=col_labels,
                         name='Partien-Δ', dtype='Float64')

    # --- Row 3: Unerklärtes Δ pro Fenster ---
    pub_vals  = row_pub.values.astype(float)
    pub_delta = np.diff(pub_vals)          # 5 Fenster-Deltas
    g_vals    = np.array([float(v) if not pd.isna(v) else 0.0
                          for v in row_game.values[1:]])
    unexpl    = pub_delta - g_vals
    row_unexpl = pd.Series([np.nan] + list(np.round(unexpl, 1)),
                            index=col_labels, name='Unerklärtes Δ', dtype='Float64')

    # --- Row 4: Kumulativ + Jahresprüfsumme Okt-07 → Okt-08 ---
    elo_start = pub_vals[0]   # Okt-07
    elo_end   = pub_vals[4]   # Okt-08 (Index 4)
    sum_game  = float(np.nansum(row_game.values[2:5]))  # Apr+Jul+Okt-08
    calculated = round(elo_start + sum_game, 1) if not np.isnan(elo_start) else np.nan
    annual_diff = round(calculated - elo_end, 1) if not np.isnan(elo_end) else np.nan

    cumulative = [elo_start]
    running = elo_start
    for v in row_game.values[1:]:
        running += float(v) if not pd.isna(v) else 0.0
        cumulative.append(round(running, 1))
    row_cumul = pd.Series(cumulative, index=col_labels,
                          name=f'Kumulativ (Δ={annual_diff:+.1f})'
                          if annual_diff is not None and not np.isnan(annual_diff)
                          else 'Kumulativ',
                          dtype='Float64')

    return pd.DataFrame([row_pub, row_game, row_unexpl, row_cumul]), annual_diff


def fmt_val(v, row_name):
    if pd.isna(v): return ''
    if row_name == 'Publiziert ELO': return f'{int(v)}'
    return f'{v:+.1f}' if v != 0 else '0.0'


def style_table(df):
    def color(val, row_name):
        if pd.isna(val) or row_name == 'Publiziert ELO': return ''
        try:
            v = float(str(val).replace('+', ''))
        except ValueError:
            return ''
        if row_name == 'Unerklärtes Δ':
            if abs(v) > 15: return 'background-color:#ff9999;font-weight:bold'
            if abs(v) > 5:  return 'background-color:#ffdd99'
        return ''

    styled = df.copy().astype(object)
    for r in df.index:
        for c in df.columns:
            styled.loc[r, c] = fmt_val(df.loc[r, c], r)

    def apply_color(df_str):
        styles = pd.DataFrame('', index=df_str.index, columns=df_str.columns)
        for r in df_str.index:
            for c in df_str.columns:
                styles.loc[r, c] = color(df.loc[r, c], r)
        cumul = [r for r in df_str.index if r.startswith('Kumulativ')]
        if cumul:
            try:
                diff = float(cumul[0].split('Δ=')[1].rstrip(')'))
                last = df_str.columns[4]   # Okt-08
                if   abs(diff) <= 3:  styles.loc[cumul[0], last] = 'background-color:#c6efce;font-weight:bold'
                elif abs(diff) <= 10: styles.loc[cumul[0], last] = 'background-color:#ffdd99;font-weight:bold'
                else:                 styles.loc[cumul[0], last] = 'background-color:#ff9999;font-weight:bold'
            except Exception:
                pass
        return styles

    return (styled.style
            .apply(apply_color, axis=None)
            .set_table_styles([
                {'selector': 'th',            'props': 'font-size:11px;padding:3px 6px'},
                {'selector': 'td',            'props': 'font-size:11px;padding:3px 6px;text-align:right'},
                {'selector': 'th.row_heading','props': 'text-align:left;min-width:120px'},
            ]))
""")

code("""
for group, grp_df in df_players.groupby('analysis_group'):
    display(HTML(f'<h2>{group} ({len(grp_df)} Spieler)</h2>'))
    for _, row in grp_df.iterrows():
        fid  = row['fide_id']
        name = row['name']
        tbl, annual_diff = build_player_table(fid)
        diff_color = ('#c6efce' if abs(annual_diff or 99) <= 3
                      else '#ffdd99' if abs(annual_diff or 99) <= 10
                      else '#ff9999')
        diff_str = (f' · Jahres-Prüfsumme Okt-07→Okt-08: '
                    f'<b style="background:{diff_color};padding:1px 4px">{annual_diff:+.1f}</b>'
                    if annual_diff is not None and not np.isnan(annual_diff) else '')
        display(HTML(f'<p style="margin:12px 0 2px"><b>{name}</b> (ID {fid}){diff_str}</p>'))
        display(style_table(tbl))
""")

md("## Zusammenfassung: Jahresprüfsumme Okt-07 → Okt-08")

code("""
jahres = []
for _, row in df_players.iterrows():
    _, diff = build_player_table(row['fide_id'])
    jahres.append({'Name': row['name'], 'Gruppe': row['analysis_group'], 'Jahres-Δ': diff})

df_j = pd.DataFrame(jahres).dropna(subset=['Jahres-Δ']).sort_values('Jahres-Δ', key=abs, ascending=False).reset_index(drop=True)
ok   = (df_j['Jahres-Δ'].abs() <= 3).sum()
warn = ((df_j['Jahres-Δ'].abs() > 3) & (df_j['Jahres-Δ'].abs() <= 10)).sum()
err  = (df_j['Jahres-Δ'].abs() > 10).sum()
print(f"Jahres-Prüfsumme Okt-07 → Okt-08:")
print(f"  ✅ OK  (|Δ| ≤ 3):  {ok}")
print(f"  ⚠️  Warn (≤ 10):    {warn}")
print(f"  ❌ Error (> 10):   {err}")
display(df_j.style
        .background_gradient(subset=['Jahres-Δ'], cmap='RdYlGn_r', vmin=-30, vmax=30)
        .format({'Jahres-Δ': '{:+.1f}'}))
""")

nb = nbf.v4.new_notebook(cells=CELLS)
nb.metadata['kernelspec'] = {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'}

out = 'notebooks/11_qc_2008_detail.ipynb'
with open(out, 'w') as f:
    nbf.write(nb, f)
print(f'Written: {out}')
