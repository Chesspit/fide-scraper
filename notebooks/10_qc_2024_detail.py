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
    # Notebook 10 — QC 2024: ELO-Detail pro Spieler

    Alle Spieler mit QC-Flag `warn` oder `error` in einem 2024-Fenster.

    **Tabellenstruktur pro Spieler:**

    | Zeile | Inhalt |
    |---|---|
    | Publiziert ELO | Monatliches FIDE-Rating Dez-23 – Dez-24 (13 Monate) |
    | Partien-Δ | Σ `rating_change_weighted` pro Monat Jan-24 – Dez-24; Dez-23-Zelle = FIDE-Korrektur Mrz-24 (nur sub-2000) |
    | Unerklärtes Δ | Differenz zwischen publiziertem Monats-Δ und Partien-Δ (inkl. Korrektur) |
    """)
    return


@app.cell
def _():
    # DB-Zugang über _setup/core (.env.notebook), QC-Abfragen über qc/
    from _setup import get_conn
    import pandas as pd

    from qc import window_detail as qwd

    PUB_START  = '2023-12-01'
    PUB_END    = '2024-12-01'
    GAME_START = '2024-01-01'
    GAME_END   = '2024-12-01'
    return GAME_END, GAME_START, PUB_END, PUB_START, get_conn, pd, qwd


@app.cell
def _(get_conn, mo, qwd):
    # Spieler mit warn/error-Fenstern, die 2024 enden (ohne Gruppe → 'ohne Gruppe')
    with get_conn() as _conn:
        df_players = qwd.players_with_findings(_conn, 2024)

    mo.vstack([
        mo.md(f"**Spieler mit 2024-Abweichungen: {len(df_players)}**"),
        df_players.groupby('analysis_group')['fide_id'].count().rename('Spieler').to_frame(),
    ])
    return (df_players,)


@app.cell
def _(GAME_END, GAME_START, PUB_END, PUB_START, df_players, get_conn, mo, qwd):
    fide_ids = df_players['fide_id'].tolist()
    with get_conn() as _conn:
        # Published ratings Dez-23 bis Dez-24, Partien-Δ Jan-24 bis Dez-24
        df_pub = qwd.published_ratings(_conn, fide_ids, qwd.month_starts(PUB_START, PUB_END))
        df_games = qwd.game_deltas(_conn, fide_ids, qwd.month_starts(GAME_START, GAME_END))
        # FIDE-Einmalkorrektur März 2024
        df_corr = qwd.corrections(_conn, fide_ids, '2024-03-01')

    mo.md(f"Published-Rating-Einträge: {len(df_pub)} · Partien-Δ-Einträge: {len(df_games)} · "
          f"FIDE-Korrekturen: {len(df_corr)}")
    return df_corr, df_games, df_pub


@app.cell
def _(GAME_END, GAME_START, PUB_END, PUB_START, df_corr, df_games, df_pub, pd):
    import numpy as np
    pub_months = pd.date_range(PUB_START, PUB_END, freq='MS')
    game_months = pd.date_range(GAME_START, GAME_END, freq='MS')

    def month_label(dt):
        return dt.strftime('%b-%y')
    col_labels = [month_label(m) for m in pub_months]

    def build_player_table(fide_id):
        """Return a (4 × 13) DataFrame for one player plus a totals-check dict."""  # Dez-23 … Dez-24
        pub = df_pub[df_pub.fide_id == fide_id].set_index('period')['published_rating'].reindex(pub_months)
        row_pub = pd.Series(pub.values, index=col_labels, name='Publiziert ELO', dtype='Float64')
        games = df_games[df_games.fide_id == fide_id].set_index('period')['game_delta'].reindex(game_months)
        row_game = pd.Series([np.nan] + list(games.values), index=col_labels, name='Partien-Δ', dtype='Float64')  # --- Row 1: Published ELO ---
        corr_row = df_corr[df_corr.fide_id == fide_id]
        fide_c = float(corr_row['fide_corr'].values[0]) if not corr_row.empty else 0.0
        if fide_c:
            row_game.iloc[0] = fide_c
        pub_vals = row_pub.values.astype(float)
        pub_delta = np.diff(pub_vals)  # --- Row 2: Partien-Δ ---
        game_vals = np.array([float(v) if not pd.isna(v) else 0.0 for v in row_game.values[1:]])
        corr_by_month = np.zeros(12)
        corr_by_month[2] = fide_c
        unexplained = pub_delta - game_vals - corr_by_month
        row_unexpl = pd.Series([np.nan] + list(np.round(unexplained, 1)), index=col_labels, name='Unerklärtes Δ', dtype='Float64')
        elo_dec23 = pub_vals[0]
        elo_dec24 = pub_vals[-1]  # FIDE correction in Dec-23 slot (for sub-2000 players)
        sum_game = float(np.nansum(row_game.values[1:]))
        calculated = round(elo_dec23 + sum_game + fide_c, 1)
        total_diff = round(calculated - elo_dec24, 1) if not np.isnan(elo_dec24) else np.nan
        cumulative = [elo_dec23]
        running = elo_dec23
        for v in row_game.values[1:]:  # --- Row 3: Unerklärtes Δ (pro Monat) ---
            running += float(v) if not pd.isna(v) else 0.0
            cumulative.append(round(running, 1))  # 12 monthly published changes
        if fide_c:
            for i in range(3, 13):
                cumulative[i] = round(cumulative[i] + fide_c, 1)
        row_cumul = pd.Series(cumulative, index=col_labels, name=f'Kumulativ (Δ={total_diff:+.1f})', dtype='Float64')  # Mar-24 = index 2
        return (pd.DataFrame([row_pub, row_game, row_unexpl, row_cumul]), total_diff)

    def fmt_val(v, row_name):
        if pd.isna(v):
            return ''  # --- Row 4: Kumulativ (Prüfsumme) ---
        if row_name == 'Publiziert ELO':  # Dec-23 ELO + Σ(Partien-Δ Jan–Dez) + FIDE-Korr = errechnet Dec-24
            return f'{int(v)}'
        return f'{v:+.1f}' if v != 0 else '0.0'

    def style_table(df):

        def color(val, row_name):
            if pd.isna(val) or val == '' or row_name == 'Publiziert ELO':  # Fill cumulative row: running total starting from Dec-23
                return ''
            try:
                v = float(str(val).replace('+', ''))
            except ValueError:
                return ''
            if row_name == 'Unerklärtes Δ':
                if abs(v) > 15:  # add correction into Mar-24 slot (index 3 in cumulative)
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
            cumul_row = [r for r in df_str.index if r.startswith('Kumulativ')]
            if cumul_row:
                last_col = df_str.columns[-1]
                try:
                    diff = float(df_str.columns[[i for i, r in enumerate(df.index) if r.startswith('Kumulativ')][0]].split('Δ=')[1].rstrip(')'))
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
        return styled.style.apply(apply_color, axis=None).set_table_styles([{'selector': 'th', 'props': 'font-size:11px; padding:3px 6px'}, {'selector': 'td', 'props': 'font-size:11px; padding:3px 6px; text-align:right'}, {'selector': 'th.row_heading', 'props': 'text-align:left; min-width:120px'}])  # Kumulativ-Zeile: letzter Wert grün wenn Δ ≈ 0, sonst orange/rot

    return build_player_table, style_table


@app.cell
def _(build_player_table, df_corr, df_players, mo, pd, style_table):
    _parts = []
    for _group, _grp_df in df_players.groupby('analysis_group'):
        _parts.append(mo.md(f'## {_group} ({len(_grp_df)} Spieler mit Abweichungen)'))
        for _, _row in _grp_df.iterrows():
            _fid = _row['fide_id']
            _corr = df_corr[df_corr.fide_id == _fid]['fide_corr'].values
            _corr_str = f' · FIDE-Korr. Mrz-24: <b>+{_corr[0]}</b>' if len(_corr) else ''
            _tbl, _total_diff = build_player_table(_fid)
            _diff_color = '#c6efce' if abs(_total_diff or 99) <= 2 else '#ffdd99' if abs(_total_diff or 99) <= 10 else '#ff9999'
            _diff_str = f' · Jahres-Prüfsumme Δ: <b style="background:{_diff_color};padding:1px 4px">{_total_diff:+.1f}</b>' if _total_diff is not None and (not pd.isna(_total_diff)) else ''
            _parts.append(mo.Html(f'<p style="margin:12px 0 2px"><b>{_row["name"]}</b> (ID {_fid}){_corr_str}{_diff_str}</p>'))
            _parts.append(mo.Html(style_table(_tbl).to_html()))
    mo.vstack(_parts)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Zusammenfassung: grösste unerklärte Abweichungen
    """)
    return


@app.cell
def _(build_player_table, df_players, mo, pd):
    _rows = []
    for _, _row in df_players.iterrows():
        _tbl, _total_diff = build_player_table(_row['fide_id'])
        for _month, _val in _tbl.loc['Unerklärtes Δ'].dropna().items():
            if abs(float(_val)) > 5:
                _rows.append({'Name': _row['name'], 'Gruppe': _row['analysis_group'], 'Monat': _month,
                              'Unerklärtes Δ': float(_val), 'Jahres-Δ': _total_diff})
    _cols = ['Name', 'Gruppe', 'Monat', 'Unerklärtes Δ', 'Jahres-Δ']
    df_summary = pd.DataFrame(_rows, columns=_cols).sort_values('Unerklärtes Δ', key=abs, ascending=False).reset_index(drop=True)

    # Prüfsumme: wie viele Spieler sind über das Jahr gesehen in Ordnung?
    jahres_check = pd.DataFrame(
        [{'Name': r['name'], 'Gruppe': r['analysis_group'], 'Jahres-Δ': build_player_table(r['fide_id'])[1]}
         for _, r in df_players.iterrows()],
        columns=['Name', 'Gruppe', 'Jahres-Δ'])
    _ok = (jahres_check['Jahres-Δ'].abs() <= 2).sum()
    _warn = ((jahres_check['Jahres-Δ'].abs() > 2) & (jahres_check['Jahres-Δ'].abs() <= 10)).sum()
    _err = (jahres_check['Jahres-Δ'].abs() > 10).sum()

    mo.vstack([
        mo.Html(df_summary.head(40).style
                .background_gradient(subset=['Unerklärtes Δ'], cmap='RdYlGn_r', vmin=-50, vmax=50)
                .background_gradient(subset=['Jahres-Δ'], cmap='RdYlGn_r', vmin=-20, vmax=20)
                .format({'Unerklärtes Δ': '{:+.1f}', 'Jahres-Δ': '{:+.1f}'}).to_html()),
        mo.md(f"**Jahres-Prüfsumme Dez-23 → Dez-24:**  \n"
              f"✅ OK (|Δ| ≤ 2): {_ok} Spieler · ⚠️ Warn (≤ 10): {_warn} Spieler · ❌ Error (> 10): {_err} Spieler"),
        mo.Html(jahres_check.sort_values('Jahres-Δ', key=abs, ascending=False).reset_index(drop=True).style
                .background_gradient(subset=['Jahres-Δ'], cmap='RdYlGn_r', vmin=-20, vmax=20)
                .format({'Jahres-Δ': '{:+.1f}'}).to_html()),
    ])
    return df_summary, jahres_check


if __name__ == "__main__":
    app.run()
