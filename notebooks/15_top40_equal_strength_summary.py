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
    # 15 — Gleichstarke & stärkere Partien je Spielerin: Übersicht

    Pro Spielerin der Top-40-Jahresende-Kohorte (2016–2025, siehe Notebook 14), über die ganze Karriere seit 2008: wie viele Partien insgesamt gegen ungefähr gleich starke Gegner (± 50 Elo, **Rating zum Zeitpunkt der jeweiligen Partie**), wie viele davon gegen Männer/Frauen, wie das direkte Ergebnis war (Siege/Remis/Niederlagen, Punktequote) und wie sich ihr Rating in diesen Partien im Schnitt verändert hat (`rating_change_weighted`). Zusätzlich: eine Jahres-Übersicht der Durchschnitts-Elo, und eine separate Auswertung für Partien gegen **mindestens 50 Elo stärkere** Gegner.
    """)
    return


@app.cell
def _():
    # marimo legt den Notebook-Ordner auf sys.path; _setup holt DB-Zugang und
    # Elo-Helfer aus core/ (pip install -e . in der .venv).
    from _setup import load_query, apply_style

    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns

    apply_style()
    pd.set_option('display.max_rows', 100)
    return load_query, np, pd, plt


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Datenbasis laden

    Drei Bausteine: (1) die volle Kohorte (auch Spielerinnen ohne jede Partie), (2) **alle** ihre Partien mit bekanntem eigenem und Gegner-Rating (keine Differenz-Einschränkung — die Buckets werden anschließend in Pandas gebildet).
    """)
    return


@app.cell
def _(load_query, pd):
    sql_cohort = '''
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
    SELECT fc.fide_id, p.name AS spielerin, p.active, p.std_rating AS aktuelles_rating
    FROM female_cohort fc JOIN players p ON p.fide_id = fc.fide_id
    '''
    cohort = load_query(sql_cohort)
    print(f'Kohorte: {len(cohort)} Spielerinnen')

    sql_games = '''
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
        gr.fide_id, gr.period, rh.std_rating AS own_rating, gr.opponent_rating,
        gr.opponent_sex, gr.result, gr.rating_change_weighted
    FROM game_results gr
    JOIN female_cohort fc        ON fc.fide_id = gr.fide_id
    LEFT JOIN rating_history rh  ON rh.fide_id = gr.fide_id AND rh.period = gr.period
    WHERE rh.std_rating IS NOT NULL
      AND gr.opponent_rating IS NOT NULL
    '''
    games = load_query(sql_games)
    games['result'] = games['result'].astype(float)
    games['rating_change_weighted'] = games['rating_change_weighted'].astype(float)
    games['own_rating'] = pd.to_numeric(games['own_rating'], errors='coerce')
    games['opponent_rating'] = pd.to_numeric(games['opponent_rating'], errors='coerce')
    games['opponent_sex'] = games['opponent_sex'].fillna('unknown')
    games['diff'] = games['opponent_rating'] - games['own_rating']
    games['year'] = pd.to_datetime(games['period']).dt.year

    equal = games[games['diff'].abs() <= 50].copy()
    stronger = games[games['diff'] >= 50].copy()
    print(f'Partien gesamt: {len(games):,}')
    print(f'  davon ± 50 Elo ("gleich stark"): {len(equal):,}')
    print(f'  davon Gegner ≥ 50 Elo stärker:   {len(stronger):,}')
    return cohort, equal, stronger


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Jahres-Übersicht — Durchschnitts-Elo der Gegner, je Gruppe

    Ø-Rating **der Gegner** pro Jahr — getrennt für die beiden Paarungs-Gruppen (± 50 Elo "gleich stark" und ≥ 50 Elo "stärker"), nicht über alle Partien gemischt. Partie-gewichtet (jede Partie zählt einmal, unabhängig davon, wie oft eine bestimmte Gegnerin/ein bestimmter Gegner vorkommt).
    """)
    return


@app.cell
def _(equal, mo, stronger):
    def yearly_opponent_avg(df_bucket):
        return df_bucket.groupby('year').agg(
            n_partien=('opponent_rating', 'size'),
            oe_rating_gegner=('opponent_rating', 'mean'),
            oe_rating_gegner_vs_f=('opponent_rating', lambda s: s[df_bucket.loc[s.index, 'opponent_sex'] == 'F'].mean()),
            oe_rating_gegner_vs_m=('opponent_rating', lambda s: s[df_bucket.loc[s.index, 'opponent_sex'] == 'M'].mean()),
        ).round(1)

    yearly_equal = yearly_opponent_avg(equal)
    yearly_stronger = yearly_opponent_avg(stronger)

    mo.vstack([
        mo.md('**Gruppe: gleich stark (± 50 Elo)**'), yearly_equal,
        mo.md('**Gruppe: stärkere Gegner (≥ 50 Elo)**'), yearly_stronger,
    ])
    return yearly_equal, yearly_stronger


@app.cell
def _(plt, yearly_equal, yearly_stronger):
    _fig, _ax = plt.subplots(figsize=(10, 5))
    _ax.plot(yearly_equal.index, yearly_equal['oe_rating_gegner'], marker='o', color='#4a7ab5', label='Ø Gegner-Rating — gleich stark (±50)')
    _ax.plot(yearly_stronger.index, yearly_stronger['oe_rating_gegner'], marker='o', color='#d7191c', label='Ø Gegner-Rating — stärker (≥50)')
    _ax.set_xlabel('Jahr')
    _ax.set_ylabel('Ø Elo der Gegner')
    _ax.set_title('Durchschnitts-Elo der Gegner pro Jahr, nach Paarungs-Gruppe')
    _ax.legend()
    plt.tight_layout()
    plt.show()
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Helper: Ergebnis-Kennzahlen je Teilmenge

    Siege (`result==1`), Remis (`result==0.5`), Niederlagen (`result==0`), Punktequote (Ø `result`) und Ø/Σ `rating_change_weighted`.
    """)
    return


@app.cell
def _(np, pd):
    def summarize(sub):
        n = len(sub)
        if n == 0:
            return pd.Series({
                'n_partien': 0, 'siege': 0, 'remis': 0, 'niederlagen': 0,
                'punktequote': np.nan, 'sum_delta': np.nan, 'mean_delta': np.nan,
            })
        return pd.Series({
            'n_partien': n,
            'siege': (sub['result'] == 1).sum(),
            'remis': (sub['result'] == 0.5).sum(),
            'niederlagen': (sub['result'] == 0).sum(),
            'punktequote': sub['result'].mean(),
            'sum_delta': sub['rating_change_weighted'].sum(),
            'mean_delta': sub['rating_change_weighted'].mean(),
        })

    def per_player_summary(df_games):
        rows = []
        for fide_id, sub in df_games.groupby('fide_id'):
            row = {'fide_id': fide_id}
            row.update({f'{k}_gesamt': v for k, v in summarize(sub).items()})
            row.update({f'{k}_vs_f': v for k, v in summarize(sub[sub.opponent_sex == 'F']).items()})
            row.update({f'{k}_vs_m': v for k, v in summarize(sub[sub.opponent_sex == 'M']).items()})
            rows.append(row)
        return pd.DataFrame(rows)

    return per_player_summary, summarize


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## A. Gleich starke Gegner (± 50 Elo)

    ### Haupttabelle — eine Zeile pro Spielerin

    Alle Spielerinnen der Kohorte; wer keine gescrapte Partie hat, erscheint mit 0/NaN.
    """)
    return


@app.cell
def _(cohort, equal, per_player_summary):
    summary_equal = per_player_summary(equal)
    table_equal = cohort.merge(summary_equal, on='fide_id', how='left')
    _n_cols = [c for c in table_equal.columns if c.startswith('n_partien') or c.split('_gesamt')[0] in ('siege', 'remis', 'niederlagen')]
    table_equal[_n_cols] = table_equal[_n_cols].fillna(0).astype(int)
    table_equal = table_equal.sort_values('n_partien_gesamt', ascending=False).reset_index(drop=True)
    display_cols = ['spielerin', 'active', 'aktuelles_rating', 'n_partien_gesamt', 'n_partien_vs_f', 'n_partien_vs_m', 'siege_gesamt', 'remis_gesamt', 'niederlagen_gesamt', 'punktequote_gesamt', 'mean_delta_gesamt', 'sum_delta_gesamt']
    table_equal[display_cols].round(3)
    return display_cols, table_equal


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Detailtabelle — Ergebnis getrennt nach Gegner-Geschlecht
    """)
    return


@app.cell
def _(table_equal):
    detail_cols = [
        'spielerin',
        'n_partien_vs_f', 'siege_vs_f', 'remis_vs_f', 'niederlagen_vs_f', 'punktequote_vs_f', 'mean_delta_vs_f',
        'n_partien_vs_m', 'siege_vs_m', 'remis_vs_m', 'niederlagen_vs_m', 'punktequote_vs_m', 'mean_delta_vs_m',
    ]
    table_equal[detail_cols].round(3)
    return (detail_cols,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Aggregat über alle Spielerinnen der Kohorte

    Beantwortet die Ausgangsfrage in einer Zahl: über alle gleich starken Gegner hinweg — schneiden diese Top-Spielerinnen gegen Männer anders ab als gegen Frauen?
    """)
    return


@app.cell
def _(equal, pd, summarize):
    agg_equal = pd.DataFrame({
        'gesamt': summarize(equal),
        'vs Frauen': summarize(equal[equal.opponent_sex == 'F']),
        'vs Männer': summarize(equal[equal.opponent_sex == 'M']),
    }).T
    agg_equal[['n_partien','siege','remis','niederlagen']] = agg_equal[['n_partien','siege','remis','niederlagen']].astype(int)
    agg_equal.round(4)
    return (agg_equal,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Chart — Punktequote vs. Frauen vs. Punktequote vs. Männer, pro Spielerin

    Jeder Punkt eine Spielerin (nur wenn sie ≥5 Partien in beiden Teilmengen hat, sonst zu verrauscht). Auf der Diagonalen: gleiche Punktequote gegen beide Geschlechter.
    """)
    return


@app.cell
def _(plt, table_equal):
    plot_df = table_equal[(table_equal.n_partien_vs_f >= 5) & (table_equal.n_partien_vs_m >= 5)].copy()
    _fig, _ax = plt.subplots(figsize=(7, 7))
    _ax.scatter(plot_df['punktequote_vs_m'], plot_df['punktequote_vs_f'], s=plot_df['n_partien_gesamt'].clip(upper=200) / 2 + 20, alpha=0.6, color='#c0587e', edgecolor='white')
    _ax.plot([0, 1], [0, 1], color='grey', lw=1, ls='--')
    _ax.set_xlim(0, 1)
    _ax.set_ylim(0, 1)
    _ax.set_xlabel('Punktequote vs. Männer (± 50 Elo)')
    _ax.set_ylabel('Punktequote vs. Frauen (± 50 Elo)')
    _ax.set_title(f'Punktequote nach Gegner-Geschlecht ({len(plot_df)} Spielerinnen mit ≥5 Partien je Seite)')
    plt.tight_layout()
    plt.show()
    print(f'Besser (oder gleich) gegen Frauen: {(plot_df.punktequote_vs_f >= plot_df.punktequote_vs_m).sum()} von {len(plot_df)}')
    print(f'Besser gegen Männer: {(plot_df.punktequote_vs_f < plot_df.punktequote_vs_m).sum()} von {len(plot_df)}')
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## B. Stärkere Gegner (≥ 50 Elo mehr als die Spielerin)

    Separate Auswertung: wie sieht das Ergebnis aus, wenn der Gegner/die Gegnerin mindestens 50 Elo-Punkte **stärker** ist als die Spielerin zum Zeitpunkt der Partie? Gleicher Tabellenaufbau wie oben, nur mit `diff >= 50` statt `|diff| <= 50`.
    """)
    return


@app.cell
def _(cohort, display_cols, per_player_summary, stronger):
    summary_stronger = per_player_summary(stronger)
    table_stronger = cohort.merge(summary_stronger, on='fide_id', how='left')
    _n_cols = [c for c in table_stronger.columns if c.startswith('n_partien') or c.split('_gesamt')[0] in ('siege', 'remis', 'niederlagen')]
    table_stronger[_n_cols] = table_stronger[_n_cols].fillna(0).astype(int)
    table_stronger = table_stronger.sort_values('n_partien_gesamt', ascending=False).reset_index(drop=True)
    table_stronger[display_cols].round(3)
    return (table_stronger,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Detailtabelle — stärkere Gegner, getrennt nach Geschlecht
    """)
    return


@app.cell
def _(detail_cols, table_stronger):
    table_stronger[detail_cols].round(3)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Aggregat — stärkere Gegner (≥ 50 Elo)
    """)
    return


@app.cell
def _(pd, stronger, summarize):
    agg_stronger = pd.DataFrame({
        'gesamt': summarize(stronger),
        'vs Frauen': summarize(stronger[stronger.opponent_sex == 'F']),
        'vs Männer': summarize(stronger[stronger.opponent_sex == 'M']),
    }).T
    agg_stronger[['n_partien','siege','remis','niederlagen']] = agg_stronger[['n_partien','siege','remis','niederlagen']].astype(int)
    agg_stronger.round(4)
    return (agg_stronger,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Vergleich: gleich stark vs. stärkere Gegner (Aggregat nebeneinander)
    """)
    return


@app.cell
def _(agg_equal, agg_stronger, pd):
    compare = pd.concat({'gleich stark (±50)': agg_equal, 'stärkere Gegner (≥50)': agg_stronger}, axis=0)
    compare.round(4)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Export
    """)
    return


@app.cell
def _(table_equal, table_stronger, yearly_equal, yearly_stronger):
    table_equal.to_csv('top40_equal_strength_summary_per_player.csv', index=False)
    table_stronger.to_csv('top40_stronger_opponent_summary_per_player.csv', index=False)
    yearly_equal.to_csv('top40_yearly_avg_opponent_rating_equal.csv')
    yearly_stronger.to_csv('top40_yearly_avg_opponent_rating_stronger.csv')
    print('wrote top40_equal_strength_summary_per_player.csv, top40_stronger_opponent_summary_per_player.csv, '
          'top40_yearly_avg_opponent_rating_equal.csv, top40_yearly_avg_opponent_rating_stronger.csv')
    return


if __name__ == "__main__":
    app.run()
