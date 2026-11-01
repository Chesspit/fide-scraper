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
    # Notebook 11 — QC 2008: ELO-Detail pro Spieler

    Alle Spieler mit QC-Flag `warn` oder `error` in einem 2008-Fenster.

    **Verfügbare Daten:**

    | Typ | Monate |
    |---|---|
    | TXT-Snapshots (published_rating) | Okt-07, Jan-08, Apr-08, Jul-08, Okt-08, Jan-09 |
    | Partiendaten (Scraping) | Apr-08, Jul-08, Okt-08 |

    **Jahresprüfsumme:** Okt-07 → Okt-08
    """)
    return


@app.cell
def _():
    # DB-Zugang über _setup/core (.env.notebook), QC-Abfragen über qc/
    from _setup import get_conn
    import numpy as np
    import pandas as pd

    from qc import window_detail as qwd

    # Snapshot- und Partien-Monate für 2008 (Quartalslisten)
    SNAP_MONTHS  = ['2007-10-01', '2008-01-01', '2008-04-01',
                    '2008-07-01', '2008-10-01', '2009-01-01']
    GAME_MONTHS  = ['2008-04-01', '2008-07-01', '2008-10-01', '2009-01-01']
    return GAME_MONTHS, SNAP_MONTHS, get_conn, np, pd, qwd


@app.cell
def _(get_conn, mo, qwd):
    # Spieler mit warn/error-Fenstern, die 2008 enden (ohne Gruppe → 'ohne Gruppe')
    with get_conn() as _conn:
        df_players = qwd.players_with_findings(_conn, 2008)

    mo.vstack([
        mo.md(f"**Spieler mit 2008-Abweichungen: {len(df_players)}**"),
        df_players.groupby('analysis_group')['fide_id'].count().rename('Spieler').to_frame(),
    ])
    return (df_players,)


@app.cell
def _(GAME_MONTHS, SNAP_MONTHS, df_players, get_conn, mo, qwd):
    with get_conn() as _conn:
        # Published ratings der Snapshot-Monate, Partien-Δ der Scraping-Monate
        df_pub = qwd.published_ratings(_conn, df_players['fide_id'].tolist(), SNAP_MONTHS)
        df_games = qwd.game_deltas(_conn, df_players['fide_id'].tolist(), GAME_MONTHS)

    mo.md(f"Published-Rating-Einträge: {len(df_pub)} · Partien-Δ-Einträge: {len(df_games)}")
    return df_games, df_pub


@app.cell
def _(GAME_MONTHS, SNAP_MONTHS, df_games, df_pub, np, pd):
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

    return build_player_table, style_table


@app.cell
def _(build_player_table, df_players, mo, np, style_table):
    _parts = []
    for _group, _grp_df in df_players.groupby('analysis_group'):
        _parts.append(mo.md(f'## {_group} ({len(_grp_df)} Spieler)'))
        for _, _row in _grp_df.iterrows():
            _fid = _row['fide_id']
            _tbl, _annual_diff = build_player_table(_fid)
            _diff_color = '#c6efce' if abs(_annual_diff or 99) <= 3 else '#ffdd99' if abs(_annual_diff or 99) <= 10 else '#ff9999'
            _diff_str = f' · Jahres-Prüfsumme Okt-07→Okt-08: <b style="background:{_diff_color};padding:1px 4px">{_annual_diff:+.1f}</b>' if _annual_diff is not None and (not np.isnan(_annual_diff)) else ''
            _parts.append(mo.Html(f'<p style="margin:12px 0 2px"><b>{_row["name"]}</b> (ID {_fid}){_diff_str}</p>'))
            _parts.append(mo.Html(style_table(_tbl).to_html()))
    mo.vstack(_parts)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Zusammenfassung: Jahresprüfsumme Okt-07 → Okt-08
    """)
    return


@app.cell
def _(build_player_table, df_players, mo, pd):
    _jahres = []
    for _, _row in df_players.iterrows():
        _, _diff = build_player_table(_row['fide_id'])
        _jahres.append({'Name': _row['name'], 'Gruppe': _row['analysis_group'], 'Jahres-Δ': _diff})
    df_j = (pd.DataFrame(_jahres, columns=['Name', 'Gruppe', 'Jahres-Δ'])
            .dropna(subset=['Jahres-Δ']).sort_values('Jahres-Δ', key=abs, ascending=False)
            .reset_index(drop=True))
    _ok = (df_j['Jahres-Δ'].abs() <= 3).sum()
    _warn = ((df_j['Jahres-Δ'].abs() > 3) & (df_j['Jahres-Δ'].abs() <= 10)).sum()
    _err = (df_j['Jahres-Δ'].abs() > 10).sum()
    mo.vstack([
        mo.md(f"**Jahres-Prüfsumme Okt-07 → Okt-08:**  \n"
              f"✅ OK (|Δ| ≤ 3): {_ok} · ⚠️ Warn (≤ 10): {_warn} · ❌ Error (> 10): {_err}"),
        mo.Html(df_j.style.background_gradient(subset=['Jahres-Δ'], cmap='RdYlGn_r', vmin=-30, vmax=30)
                .format({'Jahres-Δ': '{:+.1f}'}).to_html()),
    ])
    return (df_j,)


if __name__ == "__main__":
    app.run()
