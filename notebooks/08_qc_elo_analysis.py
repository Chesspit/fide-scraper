import marimo

__generated_with = "0.25.2"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # 08 — QC-Analyse: Rating-Delta nach ELO-Klasse und Zeitraum

    Für jeden Spieler werden die QC-Fenster ab Jan 2015 ausgewertet:
    ```
    delta = expected_change (TXT-Snapshot) − scraped_change (Σ rating_change_weighted)
    ```
    **ELO-Klassifikation** basiert auf dem Rating vom **Jan 2023** (vor der FIDE-Korrektur für Spieler unter 2000).

    Jede Pivot-Tabelle zeigt den **Prozentanteil** der Fenster pro Delta-Bucket für jeden der 23 Halbjahreszeiträume (Feb 2015 → Jan 2026).
    """)
    return


@app.cell
def _():
    # marimo legt den Notebook-Ordner auf sys.path; DB-Zugang über _setup/core,
    # QC-Abfragen über qc/ (kein eigenes QC-SQL im Notebook).
    from _setup import apply_style, get_conn

    import numpy as np
    import pandas as pd

    from qc.window_detail import windows_with_elo_group

    apply_style()
    return get_conn, np, pd, windows_with_elo_group


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Datenbasis
    """)
    return


@app.cell
def _(get_conn, mo, windows_with_elo_group):
    # QC-Fenster ab 2015, ELO-Gruppe nach letztem Rating bis Jan 2023
    with get_conn() as _conn:
        df = windows_with_elo_group(_conn, since="2015-01-01", anchor="2023-01-01").rename(
            columns={"rating_anchor": "rating_2023"})

    # Feste Perioden-Reihenfolge (alle Halbjahreszeiträume ab 2015)
    ALL_PERIODS = sorted(df['period'].unique())

    mo.vstack([
        mo.md(f"**{len(df):,} QC-Fenster geladen, {df['fide_id'].nunique():,} Spieler** — "
              "ELO-Gruppen (Basis Jan 2023):"),
        df.groupby('elo_group')['fide_id'].nunique().rename('Spieler').to_frame(),
    ])
    return ALL_PERIODS, df


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Hilfsfunktionen
    """)
    return


@app.cell
def _(ALL_PERIODS, np, pd):
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
        """Farbliche Hervorhebung: Zellen im schlechten Bereich rot, im guten grün."""
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

    return pivot_table, style_pivot


@app.cell
def _(ALL_PERIODS, np, pd):
    def top10_table(sub: pd.DataFrame, title: str) -> 'pd.io.formats.style.Styler':
        """Top-10 Spieler mit der grössten kumulativen absoluten ELO-Abweichung.
        Ausgabe: Spieler in Zeilen, Zeiträume in Spalten (delta-Wert pro Zelle).
        Letzte drei Spalten: Σ|Δ|, Ø|Δ|, Max|Δ|. Rückgabe: gestylte Tabelle.
        """
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
            lambda r: f"{r['name']} ({int(r['rating_2023']) if pd.notna(r['rating_2023']) else '?'})", axis=1
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

        return styled

    return (top10_table,)


@app.cell
def _(mo, pivot_table, style_pivot, top10_table):
    def section(sub, title: str, note: str):
        """Verteilung |delta| je Periode + Top-10 einer Teilmenge als Zellausgabe."""
        return mo.vstack([
            mo.md(f"### {title} — {note}  \n{sub['fide_id'].nunique():,} Spieler, "
                  f"{len(sub):,} Fenster"),
            mo.Html(style_pivot(pivot_table(sub, title)).to_html()),
            mo.md(f"**{title}: Top-10 nach Σ|Δ|** (delta = erwartet − gescraped, +rot / −blau)"),
            mo.Html(top10_table(sub, title).to_html()),
        ])
    return (section,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---
    ## 1 — Gesamt
    """)
    return


@app.cell
def _(df, section):
    section(df, 'Gesamt', 'alle Spieler in qc_rating_check ab 2015')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---
    ## 2 — ELO ≥ 2400
    """)
    return


@app.cell
def _(df, section):
    section(df[df['elo_group'] == 'ELO >= 2400'], 'ELO >= 2400', 'gemessen Jan 2023')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---
    ## 3 — ELO 2000–2400
    """)
    return


@app.cell
def _(df, section):
    section(df[df['elo_group'] == 'ELO 2000-2400'], 'ELO 2000-2400', 'gemessen Jan 2023')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---
    ## 4 — ELO < 2000
    """)
    return


@app.cell
def _(df, section):
    section(df[df['elo_group'] == 'ELO < 2000'], 'ELO < 2000', 'gemessen Jan 2023')
    return


if __name__ == "__main__":
    app.run()
