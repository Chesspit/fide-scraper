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
    # 14 — Top-40-Frauen (Jahresende 2016–2025) vs. Männer im Elo-Band 2400–2600

    **Nachfolger von Notebook 13.** Notebook 13 verglich die statischen Gruppen `female_top`/`male_control` (`players.analysis_group`) — die sich beim Testlauf als nur unvollständig befüllt herausstellten (`groups.backfill_status='partial'`, nur 23/66 bzw. 48/649 Spieler gelabelt, kaum aktive). Dieses Notebook ersetzt die Kohorten-Definition komplett durch eine **dynamische, survivorship-bias-freie** Ableitung aus `rating_history.published_rating` (siehe `docs/ideen_verbesserungen.md`, Abschnitte F2/F7) — keine statischen Labels, keine Pflege, kein Scraping-Backfill nötig.

    **Kohorten:**
    - **`top40_female`:** alle Frauen, die an mindestens einem der Jahresenden Dez-2016 … Dez-2025 zu den Top 40 nach `published_rating` gehörten (Union über 10 Jahre → 65 Spielerinnen, Cutoff-Rating je Jahr ~2448–2460).
    - **`male_2400_2600`:** alle Männer, deren `std_rating` zum Zeitpunkt einer Partie (2016–2025) im Band 2400–2600 lag — eine breite Vergleichspopulation im selben Elo-Band, keine Top-40-Beschränkung (sonst wäre das die absolute Weltspitze ab ~2650, ein anderes Rating-Niveau).

    **Zeitfenster:** nur Partien 2016–2025 (Zeitraum, für den die Kohorte definiert wurde) — nicht die gesamte Karriere.

    **Definitionen (wie Notebook 13):** Elo-Band = 50-Punkte-Bänder auf `own_rating`; Stärke-Bucket = ±50 Elo relativ zum Gegner; Scope = `open_mixed` vs. `women_only` (`tournament_type ∈ {women, women_team}`), nicht gefiltert sondern parallel ausgewiesen.
    """)
    return


@app.cell
def _():
    # marimo legt den Notebook-Ordner auf sys.path; _setup holt DB-Zugang und
    # Elo-Helfer aus core/ (pip install -e . in der .venv).
    from _setup import load_query, apply_style, elo_band

    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns

    apply_style()
    pd.set_option('display.max_rows', 250)

    COHORT_ORDER = ['top40_female', 'male_2400_2600']
    COHORT_PALETTE = {'top40_female': '#c0587e', 'male_2400_2600': '#4a7ab5'}
    return COHORT_ORDER, COHORT_PALETTE, elo_band, load_query, np, pd, plt, sns


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Datenbasis laden

    Zwei Teilabfragen: die Frauen-Kohorte wird per Rang (Top 40 je Jahresende) bestimmt, die Männer-Population per Elo-Band zum Partie-Zeitpunkt — beides direkt aus `rating_history`, ohne `analysis_group`.
    """)
    return


@app.cell
def _(elo_band, load_query, pd):
    sql = '''
    WITH female_ranked AS (
        SELECT rh.fide_id,
               ROW_NUMBER() OVER (PARTITION BY rh.period ORDER BY rh.published_rating DESC) AS rnk
        FROM rating_history rh
        JOIN players p ON p.fide_id = rh.fide_id
        WHERE p.sex = 'F'
          AND rh.published_rating IS NOT NULL
          AND rh.period IN ('2016-12-01','2017-12-01','2018-12-01','2019-12-01','2020-12-01',
                            '2021-12-01','2022-12-01','2023-12-01','2024-12-01','2025-12-01')
    ),
    female_cohort AS (
        SELECT DISTINCT fide_id FROM female_ranked WHERE rnk <= 40
    )
    SELECT
        gr.fide_id, 'top40_female' AS cohort,
        rh.std_rating AS own_rating, gr.opponent_rating, gr.opponent_sex, gr.result,
        gr.rating_change_weighted, gr.expected_score, gr.over_performance, gr.tournament_type
    FROM game_results gr
    JOIN female_cohort fc        ON fc.fide_id = gr.fide_id
    LEFT JOIN rating_history rh  ON rh.fide_id = gr.fide_id AND rh.period = gr.period
    WHERE gr.period BETWEEN '2016-01-01' AND '2025-12-31'
      AND gr.opponent_sex IN ('M', 'F')

    UNION ALL

    SELECT
        gr.fide_id, 'male_2400_2600' AS cohort,
        rh.std_rating AS own_rating, gr.opponent_rating, gr.opponent_sex, gr.result,
        gr.rating_change_weighted, gr.expected_score, gr.over_performance, gr.tournament_type
    FROM game_results gr
    JOIN players p               ON p.fide_id = gr.fide_id
    JOIN rating_history rh       ON rh.fide_id = gr.fide_id AND rh.period = gr.period
    WHERE p.sex = 'M'
      AND rh.std_rating BETWEEN 2400 AND 2600
      AND gr.period BETWEEN '2016-01-01' AND '2025-12-31'
      AND gr.opponent_sex IN ('M', 'F')
    '''
    raw = load_query(sql)
    n_raw = len(raw)
    df = raw.dropna(subset=['own_rating', 'opponent_rating']).copy()
    print(f'{n_raw:,} Partien geladen, {n_raw - len(df):,} ohne own_rating/opponent_rating verworfen '
          f'({(n_raw - len(df)) / n_raw:.1%}) -> {len(df):,} Partien in der Analyse')

    df['result'] = df['result'].astype(float)
    df['rating_change_weighted'] = df['rating_change_weighted'].astype(float)
    df['expected_score'] = df['expected_score'].astype(float)
    df['over_performance'] = df['over_performance'].astype(float)
    df['own_rating'] = pd.to_numeric(df['own_rating'], errors='coerce')
    df['opponent_rating'] = pd.to_numeric(df['opponent_rating'], errors='coerce')
    df['diff'] = df['opponent_rating'] - df['own_rating']

    # core.elo.elo_band (Spiegel von fn_elo_band, gleiches Format '2400-2449'/'unknown')
    df['elo_band'] = df['own_rating'].apply(elo_band)

    def strength_bucket(d):
        if pd.isna(d):
            return 'unknown'
        if d > 50:
            return 'stärker'
        if d < -50:
            return 'schwächer'
        return 'gleich'
    df['strength'] = df['diff'].apply(strength_bucket)

    df['scope'] = df['tournament_type'].apply(
        lambda t: 'women_only' if t in ('women', 'women_team') else 'open_mixed'
    )

    def band_sort_key(b):
        return (9999,) if b == 'unknown' else (int(b.split('-')[0]),)
    elo_band_order = sorted(df['elo_band'].unique(), key=band_sort_key)

    SEX_ORDER = ['F', 'M']
    STRENGTH_ORDER = ['stärker', 'gleich', 'schwächer']
    print('Elo-Bänder:', elo_band_order)
    df.head()
    return SEX_ORDER, STRENGTH_ORDER, df, elo_band_order


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Zellgrößen (Plausibilitätscheck)

    Die Frauen-Kohorte bleibt mit 65 Spielerinnen klein — Zellen an den Rändern der Elo-Range können dünn besetzt sein. Die Männer-Population ist deutlich größer (587 Spieler).
    """)
    return


@app.cell
def _(df, elo_band_order):
    qc = (
        df.groupby(['cohort', 'elo_band'])
          .agg(n_games=('fide_id', 'size'), n_players=('fide_id', 'nunique'))
          .reset_index()
    )
    qc.pivot(index='elo_band', columns='cohort', values=['n_games', 'n_players']).reindex(elo_band_order)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Helper: Metrik-Tabellen
    """)
    return


@app.cell
def _(COHORT_ORDER, SEX_ORDER, df, elo_band_order):
    def build_metrics(df_scope):
        g = df_scope.groupby(['elo_band', 'opponent_sex', 'cohort'])
        return g.agg(
            n_games=('fide_id', 'size'),
            n_players=('fide_id', 'nunique'),
            score_rate=('result', 'mean'),
            mean_expected_score=('expected_score', 'mean'),
            mean_over_performance=('over_performance', 'mean'),
            sum_rating_change_weighted=('rating_change_weighted', 'sum'),
            mean_rating_change_weighted=('rating_change_weighted', 'mean'),
        ).reset_index()

    def pivot_metric(metrics_df, value_col, round_to=4):
        tbl = metrics_df.pivot(index='elo_band', columns=['opponent_sex', 'cohort'], values=value_col)
        tbl = tbl.reindex(elo_band_order)
        cols = [(s, c) for s in SEX_ORDER for c in COHORT_ORDER if (s, c) in tbl.columns]
        return tbl[cols].round(round_to)

    metrics_all = build_metrics(df)
    metrics_open = build_metrics(df[df.scope == 'open_mixed'])
    print(f'Zellen gesamt: {len(metrics_all)}   Zellen (nur offene Turniere): {len(metrics_open)}')
    return metrics_all, metrics_open, pivot_metric


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Haupttabelle — Ø Over-Performance je Elo-Band × Gegner-Geschlecht × Kohorte

    `over_performance = result − expected_score` (Elo-Erwartung). Positiv = besser als erwartet.

    **Alle Partien:**
    """)
    return


@app.cell
def _(metrics_all, pivot_metric):
    pivot_metric(metrics_all, 'mean_over_performance')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    **Nur offene/gemischte Turniere** (ohne `women`/`women_team`):
    """)
    return


@app.cell
def _(metrics_open, pivot_metric):
    pivot_metric(metrics_open, 'mean_over_performance')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Zum Vergleich: Score-Rate und Ø rating_change_weighted (alle Partien)
    """)
    return


@app.cell
def _(metrics_all, pivot_metric):
    pivot_metric(metrics_all, 'score_rate')
    return


@app.cell
def _(metrics_all, pivot_metric):
    pivot_metric(metrics_all, 'mean_rating_change_weighted')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Heatmap: Ø Over-Performance nach Elo-Band × Gegner-Geschlecht
    """)
    return


@app.cell
def _(COHORT_ORDER, SEX_ORDER, elo_band_order, metrics_all, plt, sns):
    _fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for _ax, coh in zip(axes, COHORT_ORDER):
        sub = metrics_all[metrics_all.cohort == coh].pivot(index='elo_band', columns='opponent_sex', values='mean_over_performance').reindex(index=elo_band_order, columns=SEX_ORDER)
        sns.heatmap(sub, annot=True, fmt='.3f', cmap='RdBu_r', center=0, ax=_ax, cbar=True)
        _ax.set_title(coh)
        _ax.set_xlabel('Gegner-Geschlecht')
        _ax.set_ylabel('Elo-Band (eigenes Rating)')
    plt.tight_layout()
    plt.show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Balkendiagramm: Ø rating_change_weighted je Elo-Band, Kohorte im Vergleich
    """)
    return


@app.cell
def _(
    COHORT_ORDER,
    COHORT_PALETTE,
    SEX_ORDER,
    elo_band_order,
    metrics_all,
    plt,
):
    def bar_chart(metrics_df, title_suffix):
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
        for ax, sex in zip(axes, SEX_ORDER):
            sub = metrics_df[metrics_df.opponent_sex == sex]
            pv = (
                sub.pivot(index='elo_band', columns='cohort', values='mean_rating_change_weighted')
                .reindex(index=elo_band_order, columns=COHORT_ORDER)
            )
            pv.plot.bar(ax=ax, color=[COHORT_PALETTE[c] for c in COHORT_ORDER], edgecolor='white')
            ax.axhline(0, color='grey', lw=0.8, ls='--')
            ax.set_title(f'vs {sex} ({title_suffix})')
            ax.set_xlabel('Elo-Band (eigenes Rating)')
            ax.set_ylabel('Ø rating_change_weighted')
            ax.tick_params(axis='x', rotation=45)
        plt.tight_layout(); plt.show()

    bar_chart(metrics_all, 'alle Partien')
    return (bar_chart,)


@app.cell
def _(bar_chart, metrics_open):
    bar_chart(metrics_open, 'nur offene Turniere')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Sekundärachse: relative Gegnerstärke (±50 Elo)
    """)
    return


@app.cell
def _(COHORT_ORDER, SEX_ORDER, STRENGTH_ORDER, df):
    def build_strength_metrics(df_scope):
        g = df_scope.groupby(['strength', 'opponent_sex', 'cohort'])
        return g.agg(
            n_games=('fide_id', 'size'),
            n_players=('fide_id', 'nunique'),
            score_rate=('result', 'mean'),
            mean_over_performance=('over_performance', 'mean'),
            mean_rating_change_weighted=('rating_change_weighted', 'mean'),
        ).reset_index()

    strength_metrics = build_strength_metrics(df)
    tbl = strength_metrics.pivot(index='strength', columns=['opponent_sex', 'cohort'], values='mean_over_performance')
    tbl = tbl.reindex(STRENGTH_ORDER)
    cols = [(s, c) for s in SEX_ORDER for c in COHORT_ORDER if (s, c) in tbl.columns]
    tbl[cols].round(4)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Signifikanztest

    Wie in Notebook 13: Permutationstest auf **Spieler-Ebene** (Ø `over_performance` pro `fide_id`, nicht pro Partie), um den Cluster-Effekt durch stark ungleiche Partienzahlen pro Person zu vermeiden. 10.000 Permutationen, fester Seed, kein scipy/statsmodels nötig. Zellen mit < 8 Spielern in einer Kohorte werden als `underpowered` markiert.
    """)
    return


@app.cell
def _(SEX_ORDER, df, elo_band_order, np, pd):
    def permutation_test(a, b, n_perm=10000, seed=42, return_diffs=False):
        rng = np.random.default_rng(seed)
        observed = a.mean() - b.mean()
        pooled = np.concatenate([a, b])
        n_a = len(a)
        diffs = np.empty(n_perm)
        for i in range(n_perm):
            rng.shuffle(pooled)
            diffs[i] = pooled[:n_a].mean() - pooled[n_a:].mean()
        p_value = (np.abs(diffs) >= np.abs(observed)).mean()
        return (observed, p_value, diffs) if return_diffs else (observed, p_value)

    def player_level_means(df_scope, value_col):
        return df_scope.groupby(['cohort', 'fide_id'])[value_col].mean().reset_index()
    SCOPES = {'all': df, 'open_mixed': df[df.scope == 'open_mixed']}
    sig_rows = []
    for scope_name, dsub in SCOPES.items():
        for band in elo_band_order:
            if band == 'unknown':
                continue
            for sex in SEX_ORDER:
                _cell = dsub[(dsub.elo_band == band) & (dsub.opponent_sex == sex)]
                _pm = player_level_means(_cell, 'over_performance')
                _a = _pm.loc[_pm.cohort == 'top40_female', 'over_performance'].values
                _b = _pm.loc[_pm.cohort == 'male_2400_2600', 'over_performance'].values
                if len(_a) == 0 or len(_b) == 0:
                    continue
                diff, p = permutation_test(_a, _b)
                sig_rows.append({'scope': scope_name, 'elo_band': band, 'opponent_sex': sex, 'n_players_female': len(_a), 'n_players_male': len(_b), 'mean_diff': round(diff, 4), 'p_value': round(p, 4), 'underpowered': len(_a) < 8 or len(_b) < 8})
    sig_cols = ['scope', 'elo_band', 'opponent_sex', 'n_players_female', 'n_players_male', 'mean_diff', 'p_value', 'underpowered']
    sig_table = pd.DataFrame(sig_rows, columns=sig_cols)
    if sig_table.empty:
        print('Keine Zelle hat aktuell Spieler in beiden Kohorten.')
    sig_table
    return permutation_test, player_level_means, sig_table


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Zur Veranschaulichung: Nullverteilung der am besten besetzten Zelle
    """)
    return


@app.cell
def _(
    COHORT_PALETTE,
    df,
    permutation_test,
    player_level_means,
    plt,
    sig_table,
):
    all_scope = sig_table[sig_table.scope == 'all']
    if all_scope.empty:
        print('Übersprungen: keine Zelle mit Daten in beiden Kohorten.')
    else:
        best = all_scope.sort_values(['n_players_female', 'n_players_male'], ascending=False).iloc[0]
        _cell = df[(df.elo_band == best.elo_band) & (df.opponent_sex == best.opponent_sex)]
        _pm = player_level_means(_cell, 'over_performance')
        _a = _pm.loc[_pm.cohort == 'top40_female', 'over_performance'].values
        _b = _pm.loc[_pm.cohort == 'male_2400_2600', 'over_performance'].values
        observed, p_value, diffs = permutation_test(_a, _b, return_diffs=True)
        _fig, _ax = plt.subplots()
        _ax.hist(diffs, bins=50, color='#888888', alpha=0.8)
        _ax.axvline(observed, color=COHORT_PALETTE['top40_female'], lw=2, label=f'beobachtet ({observed:+.3f}), p={p_value:.4f}')
        _ax.set_title(f'Permutations-Nullverteilung: {best.elo_band}, vs {best.opponent_sex}')
        _ax.set_xlabel('Differenz der Spieler-Mittelwerte (top40_female − male_2400_2600)')
        _ax.legend()
        plt.tight_layout()
        plt.show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Fazit

    Die Tabellen in Abschnitt 2 zeigen direkt, ob die Top-40-Frauen der letzten zehn Jahre bei gleichem absoluten Elo-Niveau besser oder schlechter abschneiden als die Vergleichspopulation der Männer im selben Band — getrennt nach Gegner-Geschlecht und mit/ohne Frauen-only-Turniere. Der Permutationstest in Abschnitt 6 zeigt, welche dieser Unterschiede über reine Stichprobenstreuung hinausgehen.

    **Caveats:**
    - Die Frauen-Kohorte bleibt mit 65 Spielerinnen (52 mit Partien im Fenster 2016–2025) klein — Zellen an den Rändern der Elo-Range sind oft `underpowered`.
    - `male_2400_2600` ist eine reine Elo-Band-Population, keine Top-40-Auswahl — das ist beabsichtigt (Top-40-Männer wären ~2650+, ein anderes Niveau), macht die beiden Kohorten aber nicht symmetrisch definiert.
    - Der Frauen-Turnier-Bias bleibt in der `all`-Sicht enthalten; `open_mixed` ist die fairere Vergleichsbasis, hat aber pro Zelle weniger Partien.
    """)
    return


if __name__ == "__main__":
    app.run()
