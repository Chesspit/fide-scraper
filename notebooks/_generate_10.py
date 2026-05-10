#!/usr/bin/env python3
"""Generate notebooks/10_qc_2024_detail.ipynb

Per player with 2024 QC warn/error:
  Row 1 — Published ELO:   Dec-23 … Dec-24 (13 months)
  Row 2 — Game Δ:          [FIDE-Korr.] Jan-24 … Dec-24
              Dec-23 cell shows FIDE March-2024 correction for sub-2000 players
  Row 3 — Unexplained Δ:   published_Δ − game_Δ − fide_corr per month
"""
import nbformat as nbf

CELLS = []


def code(src):
    CELLS.append(nbf.v4.new_code_cell(src.strip()))


def md(src):
    CELLS.append(nbf.v4.new_markdown_cell(src.strip()))


# ── Title ────────────────────────────────────────────────────────────────────
md("""# Notebook 10 — QC 2024: ELO-Detail pro Spieler

Alle Spieler mit QC-Flag `warn` oder `error` in einem 2024-Fenster.

**Tabellenstruktur pro Spieler:**
| Zeile | Inhalt |
|---|---|
| Publiziert ELO | Monatliches FIDE-Rating Dez-23 – Dez-24 (13 Monate) |
| Partien-Δ | Σ `rating_change_weighted` pro Monat Jan-24 – Dez-24; Dez-23-Zelle = FIDE-Korrektur Mrz-24 (nur sub-2000) |
| Unerklärtes Δ | Differenz zwischen publiziertem Monats-Δ und Partien-Δ (inkl. Korrektur) |
""")

# ── Setup ────────────────────────────────────────────────────────────────────
code("""
import os
import warnings
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from IPython.display import display, HTML

warnings.filterwarnings('ignore')
load_dotenv('../.env.notebook')
conn = psycopg2.connect(os.getenv('DATABASE_URL'))

PUB_START  = '2023-12-01'
PUB_END    = '2024-12-01'
GAME_START = '2024-01-01'
GAME_END   = '2024-12-01'
""")

# ── Load data ─────────────────────────────────────────────────────────────────
code("""
# Players with 2024 warn/error
df_players = pd.read_sql(\"\"\"
    SELECT DISTINCT q.fide_id, p.name, p.analysis_group,
           MAX(q.flag) FILTER (WHERE q.flag = 'error') IS NOT NULL AS has_error
    FROM qc_rating_check q
    JOIN players p USING (fide_id)
    WHERE EXTRACT(YEAR FROM q.period_end) = 2024
      AND q.flag IN ('warn', 'error')
    GROUP BY q.fide_id, p.name, p.analysis_group
    ORDER BY p.analysis_group, p.name
\"\"\", conn)

print(f"Spieler mit 2024-Abweichungen: {len(df_players)}")
print(df_players.groupby('analysis_group')['fide_id'].count().to_string())
""")

code("""
fide_ids = df_players['fide_id'].tolist()
ids_sql = ','.join(str(i) for i in fide_ids)

# Published ratings Dec-23 to Dec-24
df_pub = pd.read_sql(f\"\"\"
    SELECT fide_id, period, published_rating
    FROM rating_history
    WHERE period BETWEEN '{PUB_START}' AND '{PUB_END}'
      AND fide_id IN ({ids_sql})
      AND published_rating IS NOT NULL
\"\"\", conn)
df_pub['period'] = pd.to_datetime(df_pub['period'])

# Game changes Jan-24 to Dec-24
df_games = pd.read_sql(f\"\"\"
    SELECT fide_id, period, ROUND(SUM(rating_change_weighted)::numeric, 1) AS game_delta
    FROM game_results
    WHERE period BETWEEN '{GAME_START}' AND '{GAME_END}'
      AND fide_id IN ({ids_sql})
    GROUP BY fide_id, period
\"\"\", conn)
df_games['period'] = pd.to_datetime(df_games['period'])

# FIDE March 2024 corrections
df_corr = pd.read_sql(f\"\"\"
    SELECT fide_id, amount AS fide_corr
    FROM rating_corrections
    WHERE period = '2024-03-01'
      AND fide_id IN ({ids_sql})
\"\"\", conn)

print(f"Published-Rating-Einträge: {len(df_pub)}")
print(f"Partien-Δ-Einträge:        {len(df_games)}")
print(f"FIDE-Korrekturen:          {len(df_corr)}")
""")

# ── Build display table ───────────────────────────────────────────────────────
code("""
import pandas as pd
import numpy as np

pub_months  = pd.date_range(PUB_START, PUB_END, freq='MS')
game_months = pd.date_range(GAME_START, GAME_END, freq='MS')

def month_label(dt):
    return dt.strftime('%b-%y')

col_labels = [month_label(m) for m in pub_months]   # Dez-23 … Dez-24

def build_player_table(fide_id):
    \"\"\"Return a (4 × 13) DataFrame for one player plus a totals-check dict.\"\"\"
    # --- Row 1: Published ELO ---
    pub = (df_pub[df_pub.fide_id == fide_id]
           .set_index('period')['published_rating']
           .reindex(pub_months))
    row_pub = pd.Series(pub.values, index=col_labels, name='Publiziert ELO', dtype='Float64')

    # --- Row 2: Partien-Δ ---
    games = (df_games[df_games.fide_id == fide_id]
             .set_index('period')['game_delta']
             .reindex(game_months))
    row_game = pd.Series([np.nan] + list(games.values),
                         index=col_labels, name='Partien-Δ', dtype='Float64')

    # FIDE correction in Dec-23 slot (for sub-2000 players)
    corr_row = df_corr[df_corr.fide_id == fide_id]
    fide_c = float(corr_row['fide_corr'].values[0]) if not corr_row.empty else 0.0
    if fide_c:
        row_game.iloc[0] = fide_c

    # --- Row 3: Unerklärtes Δ (pro Monat) ---
    pub_vals = row_pub.values.astype(float)
    pub_delta = np.diff(pub_vals)          # 12 monthly published changes
    game_vals = np.array([float(v) if not pd.isna(v) else 0.0
                          for v in row_game.values[1:]])
    corr_by_month = np.zeros(12)
    corr_by_month[2] = fide_c             # Mar-24 = index 2
    unexplained = pub_delta - game_vals - corr_by_month
    row_unexpl = pd.Series([np.nan] + list(np.round(unexplained, 1)),
                            index=col_labels, name='Unerklärtes Δ', dtype='Float64')

    # --- Row 4: Kumulativ (Prüfsumme) ---
    # Dec-23 ELO + Σ(Partien-Δ Jan–Dez) + FIDE-Korr = errechnet Dec-24
    elo_dec23 = pub_vals[0]
    elo_dec24 = pub_vals[-1]
    sum_game  = float(np.nansum(row_game.values[1:]))
    calculated = round(elo_dec23 + sum_game + fide_c, 1)
    total_diff = round(calculated - elo_dec24, 1) if not np.isnan(elo_dec24) else np.nan

    # Fill cumulative row: running total starting from Dec-23
    cumulative = [elo_dec23]
    running = elo_dec23
    for v in row_game.values[1:]:
        running += float(v) if not pd.isna(v) else 0.0
        cumulative.append(round(running, 1))
    if fide_c:
        # add correction into Mar-24 slot (index 3 in cumulative)
        for i in range(3, 13):
            cumulative[i] = round(cumulative[i] + fide_c, 1)
    row_cumul = pd.Series(cumulative, index=col_labels,
                          name=f'Kumulativ (Δ={total_diff:+.1f})', dtype='Float64')

    return pd.DataFrame([row_pub, row_game, row_unexpl, row_cumul]), total_diff


def fmt_val(v, row_name):
    if pd.isna(v):
        return ''
    if row_name == 'Publiziert ELO':
        return f'{int(v)}'
    return f'{v:+.1f}' if v != 0 else '0.0'


def style_table(df):
    def color(val, row_name):
        if pd.isna(val) or val == '' or row_name == 'Publiziert ELO':
            return ''
        try:
            v = float(str(val).replace('+', ''))
        except ValueError:
            return ''
        if row_name == 'Unerklärtes Δ':
            if abs(v) > 15:
                return 'background-color: #ff9999; font-weight: bold'
            if abs(v) > 5:
                return 'background-color: #ffdd99'
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
        # Kumulativ-Zeile: letzter Wert grün wenn Δ ≈ 0, sonst orange/rot
        cumul_row = [r for r in df_str.index if r.startswith('Kumulativ')]
        if cumul_row:
            last_col = df_str.columns[-1]
            try:
                diff = float(df_str.columns[
                    [i for i, r in enumerate(df.index) if r.startswith('Kumulativ')][0]
                ].split('Δ=')[1].rstrip(')'))
            except Exception:
                diff = None
            if diff is not None:
                if abs(diff) <= 2:
                    styles.loc[cumul_row[0], last_col] = 'background-color:#c6efce; font-weight:bold'
                elif abs(diff) <= 10:
                    styles.loc[cumul_row[0], last_col] = 'background-color:#ffdd99; font-weight:bold'
                else:
                    styles.loc[cumul_row[0], last_col] = 'background-color:#ff9999; font-weight:bold'
        return styles

    return (styled.style
            .apply(apply_color, axis=None)
            .set_table_styles([
                {'selector': 'th', 'props': 'font-size:11px; padding:3px 6px'},
                {'selector': 'td', 'props': 'font-size:11px; padding:3px 6px; text-align:right'},
                {'selector': 'th.row_heading', 'props': 'text-align:left; min-width:120px'},
            ]))
""")

# ── Display per group ─────────────────────────────────────────────────────────
code("""
for group, grp_df in df_players.groupby('analysis_group'):
    display(HTML(f'<h2>{group} ({len(grp_df)} Spieler mit Abweichungen)</h2>'))
    for _, row in grp_df.iterrows():
        fid   = row['fide_id']
        name  = row['name']
        corr  = df_corr[df_corr.fide_id == fid]['fide_corr'].values
        corr_str = f' · FIDE-Korr. Mrz-24: <b>+{corr[0]}</b>' if len(corr) else ''
        tbl, total_diff = build_player_table(fid)
        diff_color = '#c6efce' if abs(total_diff or 99) <= 2 else ('#ffdd99' if abs(total_diff or 99) <= 10 else '#ff9999')
        diff_str = f' · Jahres-Prüfsumme Δ: <b style="background:{diff_color};padding:1px 4px">{total_diff:+.1f}</b>' if total_diff is not None and not pd.isna(total_diff) else ''
        display(HTML(f'<p style=\"margin:12px 0 2px\"><b>{name}</b> (ID {fid}){corr_str}{diff_str}</p>'))
        display(style_table(tbl))
""")

# ── Summary: worst unexplained deltas ────────────────────────────────────────
md("## Zusammenfassung: grösste unerklärte Abweichungen")

code("""
rows = []
for _, row in df_players.iterrows():
    fid = row['fide_id']
    tbl, total_diff = build_player_table(fid)
    unexpl = tbl.loc['Unerklärtes Δ'].dropna()
    for month, val in unexpl.items():
        if abs(float(val)) > 5:
            rows.append({
                'Name':           row['name'],
                'Gruppe':         row['analysis_group'],
                'Monat':          month,
                'Unerklärtes Δ':  float(val),
                'Jahres-Δ':       total_diff,
            })

df_summary = (pd.DataFrame(rows)
              .sort_values('Unerklärtes Δ', key=abs, ascending=False)
              .reset_index(drop=True))
display(df_summary.head(40).style
        .background_gradient(subset=['Unerklärtes Δ'], cmap='RdYlGn_r', vmin=-50, vmax=50)
        .background_gradient(subset=['Jahres-Δ'], cmap='RdYlGn_r', vmin=-20, vmax=20)
        .format({'Unerklärtes Δ': '{:+.1f}', 'Jahres-Δ': '{:+.1f}'}))

# Prüfsumme: wie viele Spieler sind über das Jahr gesehen in Ordnung?
jahres_check = pd.DataFrame([
    {'Name': r['name'], 'Gruppe': r['analysis_group'],
     'Jahres-Δ': build_player_table(r['fide_id'])[1]}
    for _, r in df_players.iterrows()
])
ok  = (jahres_check['Jahres-Δ'].abs() <= 2).sum()
warn = ((jahres_check['Jahres-Δ'].abs() > 2) & (jahres_check['Jahres-Δ'].abs() <= 10)).sum()
err = (jahres_check['Jahres-Δ'].abs() > 10).sum()
print(f"\\nJahres-Prüfsumme Dez-23 → Dez-24:")
print(f"  ✅ OK  (|Δ| ≤ 2):   {ok} Spieler")
print(f"  ⚠️  Warn (≤ 10):     {warn} Spieler")
print(f"  ❌ Error (> 10):    {err} Spieler")
display(jahres_check.sort_values('Jahres-Δ', key=abs, ascending=False)
        .reset_index(drop=True)
        .style.background_gradient(subset=['Jahres-Δ'], cmap='RdYlGn_r', vmin=-20, vmax=20)
        .format({'Jahres-Δ': '{:+.1f}'}))
""")

# ── Write notebook ────────────────────────────────────────────────────────────
nb = nbf.v4.new_notebook(cells=CELLS)
nb.metadata['kernelspec'] = {
    'display_name': 'Python 3',
    'language': 'python',
    'name': 'python3',
}

out = 'notebooks/10_qc_2024_detail.ipynb'
with open(out, 'w') as f:
    nbf.write(nb, f)
print(f'Written: {out}')
