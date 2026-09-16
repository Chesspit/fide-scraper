"""Generate notebook 15_top40_equal_strength_summary.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_15.py
"""

from pathlib import Path
import nbformat as nbf

NBDIR = Path(__file__).resolve().parent

BOILERPLATE = [
    "import sys",
    "from pathlib import Path",
    "sys.path.insert(0, str(Path.cwd()))",
    "from _setup import load_query, apply_style",
    "",
    "import numpy as np",
    "import pandas as pd",
    "import matplotlib.pyplot as plt",
    "import seaborn as sns",
    "",
    "apply_style()",
    "pd.set_option('display.max_rows', 100)",
]


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


nb15 = [
    ("md", "# 15 — Gleichstarke & stärkere Partien je Spielerin: Übersicht\n\n"
           "Pro Spielerin der Top-40-Jahresende-Kohorte (2016–2025, siehe Notebook 14), über "
           "die ganze Karriere seit 2008: wie viele Partien insgesamt gegen ungefähr gleich "
           "starke Gegner (± 50 Elo, **Rating zum Zeitpunkt der jeweiligen Partie**), wie "
           "viele davon gegen Männer/Frauen, wie das direkte Ergebnis war "
           "(Siege/Remis/Niederlagen, Punktequote) und wie sich ihr Rating in diesen Partien "
           "im Schnitt verändert hat (`rating_change_weighted`). Zusätzlich: eine "
           "Jahres-Übersicht der Durchschnitts-Elo, und eine separate Auswertung für Partien "
           "gegen **mindestens 50 Elo stärkere** Gegner."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Datenbasis laden\n\n"
           "Drei Bausteine: (1) die volle Kohorte (auch Spielerinnen ohne jede Partie), "
           "(2) **alle** ihre Partien mit bekanntem eigenem und Gegner-Rating (keine "
           "Differenz-Einschränkung — die Buckets werden anschließend in Pandas gebildet)."),
    ("code",
        "sql_cohort = '''\n"
        "WITH female_ranked AS (\n"
        "    SELECT rh.fide_id,\n"
        "           ROW_NUMBER() OVER (PARTITION BY rh.period ORDER BY rh.published_rating DESC) AS rnk\n"
        "    FROM rating_history rh\n"
        "    JOIN players p ON p.fide_id = rh.fide_id\n"
        "    WHERE p.sex = 'F'\n"
        "      AND rh.published_rating IS NOT NULL\n"
        "      AND rh.period IN ('2016-12-01','2017-12-01','2018-12-01','2019-12-01','2020-12-01',\n"
        "                        '2021-12-01','2022-12-01','2023-12-01','2024-12-01','2025-12-01')\n"
        "),\n"
        "female_cohort AS (\n"
        "    SELECT DISTINCT fide_id FROM female_ranked WHERE rnk <= 40\n"
        ")\n"
        "SELECT fc.fide_id, p.name AS spielerin, p.active, p.std_rating AS aktuelles_rating\n"
        "FROM female_cohort fc JOIN players p ON p.fide_id = fc.fide_id\n"
        "'''\n"
        "cohort = load_query(sql_cohort)\n"
        "print(f'Kohorte: {len(cohort)} Spielerinnen')\n"
        "\n"
        "sql_games = '''\n"
        "WITH female_ranked AS (\n"
        "    SELECT rh.fide_id,\n"
        "           ROW_NUMBER() OVER (PARTITION BY rh.period ORDER BY rh.published_rating DESC) AS rnk\n"
        "    FROM rating_history rh\n"
        "    JOIN players p ON p.fide_id = rh.fide_id\n"
        "    WHERE p.sex = 'F'\n"
        "      AND rh.published_rating IS NOT NULL\n"
        "      AND rh.period IN ('2016-12-01','2017-12-01','2018-12-01','2019-12-01','2020-12-01',\n"
        "                        '2021-12-01','2022-12-01','2023-12-01','2024-12-01','2025-12-01')\n"
        "),\n"
        "female_cohort AS (\n"
        "    SELECT DISTINCT fide_id FROM female_ranked WHERE rnk <= 40\n"
        ")\n"
        "SELECT\n"
        "    gr.fide_id, gr.period, rh.std_rating AS own_rating, gr.opponent_rating,\n"
        "    gr.opponent_sex, gr.result, gr.rating_change_weighted\n"
        "FROM game_results gr\n"
        "JOIN female_cohort fc        ON fc.fide_id = gr.fide_id\n"
        "LEFT JOIN rating_history rh  ON rh.fide_id = gr.fide_id AND rh.period = gr.period\n"
        "WHERE rh.std_rating IS NOT NULL\n"
        "  AND gr.opponent_rating IS NOT NULL\n"
        "'''\n"
        "games = load_query(sql_games)\n"
        "games['result'] = games['result'].astype(float)\n"
        "games['rating_change_weighted'] = games['rating_change_weighted'].astype(float)\n"
        "games['own_rating'] = pd.to_numeric(games['own_rating'], errors='coerce')\n"
        "games['opponent_rating'] = pd.to_numeric(games['opponent_rating'], errors='coerce')\n"
        "games['opponent_sex'] = games['opponent_sex'].fillna('unknown')\n"
        "games['diff'] = games['opponent_rating'] - games['own_rating']\n"
        "games['year'] = pd.to_datetime(games['period']).dt.year\n"
        "\n"
        "equal = games[games['diff'].abs() <= 50].copy()\n"
        "stronger = games[games['diff'] >= 50].copy()\n"
        "print(f'Partien gesamt: {len(games):,}')\n"
        "print(f'  davon ± 50 Elo (\"gleich stark\"): {len(equal):,}')\n"
        "print(f'  davon Gegner ≥ 50 Elo stärker:   {len(stronger):,}')"),

    ("md", "## Jahres-Übersicht — Durchschnitts-Elo der Gegner, je Gruppe\n\n"
           "Ø-Rating **der Gegner** pro Jahr — getrennt für die beiden Paarungs-Gruppen "
           "(± 50 Elo \"gleich stark\" und ≥ 50 Elo \"stärker\"), nicht über alle Partien "
           "gemischt. Partie-gewichtet (jede Partie zählt einmal, unabhängig davon, wie oft "
           "eine bestimmte Gegnerin/ein bestimmter Gegner vorkommt)."),
    ("code",
        "def yearly_opponent_avg(df_bucket):\n"
        "    return df_bucket.groupby('year').agg(\n"
        "        n_partien=('opponent_rating', 'size'),\n"
        "        oe_rating_gegner=('opponent_rating', 'mean'),\n"
        "        oe_rating_gegner_vs_f=('opponent_rating', lambda s: s[df_bucket.loc[s.index, 'opponent_sex'] == 'F'].mean()),\n"
        "        oe_rating_gegner_vs_m=('opponent_rating', lambda s: s[df_bucket.loc[s.index, 'opponent_sex'] == 'M'].mean()),\n"
        "    ).round(1)\n"
        "\n"
        "yearly_equal = yearly_opponent_avg(equal)\n"
        "yearly_stronger = yearly_opponent_avg(stronger)\n"
        "\n"
        "print('Gruppe: gleich stark (± 50 Elo)')\n"
        "display(yearly_equal)\n"
        "print('\\nGruppe: stärkere Gegner (≥ 50 Elo)')\n"
        "display(yearly_stronger)"),

    ("code",
        "fig, ax = plt.subplots(figsize=(10, 5))\n"
        "ax.plot(yearly_equal.index, yearly_equal['oe_rating_gegner'], marker='o', color='#4a7ab5', label='Ø Gegner-Rating — gleich stark (±50)')\n"
        "ax.plot(yearly_stronger.index, yearly_stronger['oe_rating_gegner'], marker='o', color='#d7191c', label='Ø Gegner-Rating — stärker (≥50)')\n"
        "ax.set_xlabel('Jahr')\n"
        "ax.set_ylabel('Ø Elo der Gegner')\n"
        "ax.set_title('Durchschnitts-Elo der Gegner pro Jahr, nach Paarungs-Gruppe')\n"
        "ax.legend()\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Helper: Ergebnis-Kennzahlen je Teilmenge\n\n"
           "Siege (`result==1`), Remis (`result==0.5`), Niederlagen (`result==0`), Punktequote "
           "(Ø `result`) und Ø/Σ `rating_change_weighted`."),
    ("code",
        "def summarize(sub):\n"
        "    n = len(sub)\n"
        "    if n == 0:\n"
        "        return pd.Series({\n"
        "            'n_partien': 0, 'siege': 0, 'remis': 0, 'niederlagen': 0,\n"
        "            'punktequote': np.nan, 'sum_delta': np.nan, 'mean_delta': np.nan,\n"
        "        })\n"
        "    return pd.Series({\n"
        "        'n_partien': n,\n"
        "        'siege': (sub['result'] == 1).sum(),\n"
        "        'remis': (sub['result'] == 0.5).sum(),\n"
        "        'niederlagen': (sub['result'] == 0).sum(),\n"
        "        'punktequote': sub['result'].mean(),\n"
        "        'sum_delta': sub['rating_change_weighted'].sum(),\n"
        "        'mean_delta': sub['rating_change_weighted'].mean(),\n"
        "    })\n"
        "\n"
        "def per_player_summary(df_games):\n"
        "    rows = []\n"
        "    for fide_id, sub in df_games.groupby('fide_id'):\n"
        "        row = {'fide_id': fide_id}\n"
        "        row.update({f'{k}_gesamt': v for k, v in summarize(sub).items()})\n"
        "        row.update({f'{k}_vs_f': v for k, v in summarize(sub[sub.opponent_sex == 'F']).items()})\n"
        "        row.update({f'{k}_vs_m': v for k, v in summarize(sub[sub.opponent_sex == 'M']).items()})\n"
        "        rows.append(row)\n"
        "    return pd.DataFrame(rows)"),

    ("md", "## A. Gleich starke Gegner (± 50 Elo)\n\n"
           "### Haupttabelle — eine Zeile pro Spielerin\n\n"
           "Alle Spielerinnen der Kohorte; wer keine gescrapte Partie hat, erscheint mit 0/NaN."),
    ("code",
        "summary_equal = per_player_summary(equal)\n"
        "table_equal = cohort.merge(summary_equal, on='fide_id', how='left')\n"
        "n_cols = [c for c in table_equal.columns if c.startswith('n_partien') or c.split('_gesamt')[0] in ('siege','remis','niederlagen')]\n"
        "table_equal[n_cols] = table_equal[n_cols].fillna(0).astype(int)\n"
        "table_equal = table_equal.sort_values('n_partien_gesamt', ascending=False).reset_index(drop=True)\n"
        "\n"
        "display_cols = [\n"
        "    'spielerin', 'active', 'aktuelles_rating',\n"
        "    'n_partien_gesamt', 'n_partien_vs_f', 'n_partien_vs_m',\n"
        "    'siege_gesamt', 'remis_gesamt', 'niederlagen_gesamt', 'punktequote_gesamt',\n"
        "    'mean_delta_gesamt', 'sum_delta_gesamt',\n"
        "]\n"
        "table_equal[display_cols].round(3)"),

    ("md", "### Detailtabelle — Ergebnis getrennt nach Gegner-Geschlecht"),
    ("code",
        "detail_cols = [\n"
        "    'spielerin',\n"
        "    'n_partien_vs_f', 'siege_vs_f', 'remis_vs_f', 'niederlagen_vs_f', 'punktequote_vs_f', 'mean_delta_vs_f',\n"
        "    'n_partien_vs_m', 'siege_vs_m', 'remis_vs_m', 'niederlagen_vs_m', 'punktequote_vs_m', 'mean_delta_vs_m',\n"
        "]\n"
        "table_equal[detail_cols].round(3)"),

    ("md", "### Aggregat über alle Spielerinnen der Kohorte\n\n"
           "Beantwortet die Ausgangsfrage in einer Zahl: über alle gleich starken Gegner "
           "hinweg — schneiden diese Top-Spielerinnen gegen Männer anders ab als gegen "
           "Frauen?"),
    ("code",
        "agg_equal = pd.DataFrame({\n"
        "    'gesamt': summarize(equal),\n"
        "    'vs Frauen': summarize(equal[equal.opponent_sex == 'F']),\n"
        "    'vs Männer': summarize(equal[equal.opponent_sex == 'M']),\n"
        "}).T\n"
        "agg_equal[['n_partien','siege','remis','niederlagen']] = agg_equal[['n_partien','siege','remis','niederlagen']].astype(int)\n"
        "agg_equal.round(4)"),

    ("md", "### Chart — Punktequote vs. Frauen vs. Punktequote vs. Männer, pro Spielerin\n\n"
           "Jeder Punkt eine Spielerin (nur wenn sie ≥5 Partien in beiden Teilmengen hat, "
           "sonst zu verrauscht). Auf der Diagonalen: gleiche Punktequote gegen beide "
           "Geschlechter."),
    ("code",
        "plot_df = table_equal[(table_equal.n_partien_vs_f >= 5) & (table_equal.n_partien_vs_m >= 5)].copy()\n"
        "fig, ax = plt.subplots(figsize=(7, 7))\n"
        "ax.scatter(plot_df['punktequote_vs_m'], plot_df['punktequote_vs_f'],\n"
        "           s=plot_df['n_partien_gesamt'].clip(upper=200) / 2 + 20,\n"
        "           alpha=0.6, color='#c0587e', edgecolor='white')\n"
        "ax.plot([0, 1], [0, 1], color='grey', lw=1, ls='--')\n"
        "ax.set_xlim(0, 1); ax.set_ylim(0, 1)\n"
        "ax.set_xlabel('Punktequote vs. Männer (± 50 Elo)')\n"
        "ax.set_ylabel('Punktequote vs. Frauen (± 50 Elo)')\n"
        "ax.set_title(f'Punktequote nach Gegner-Geschlecht ({len(plot_df)} Spielerinnen mit ≥5 Partien je Seite)')\n"
        "plt.tight_layout(); plt.show()\n"
        "\n"
        "print(f'Besser (oder gleich) gegen Frauen: {(plot_df.punktequote_vs_f >= plot_df.punktequote_vs_m).sum()} von {len(plot_df)}')\n"
        "print(f'Besser gegen Männer: {(plot_df.punktequote_vs_f < plot_df.punktequote_vs_m).sum()} von {len(plot_df)}')"),

    ("md", "## B. Stärkere Gegner (≥ 50 Elo mehr als die Spielerin)\n\n"
           "Separate Auswertung: wie sieht das Ergebnis aus, wenn der Gegner/die Gegnerin "
           "mindestens 50 Elo-Punkte **stärker** ist als die Spielerin zum Zeitpunkt der "
           "Partie? Gleicher Tabellenaufbau wie oben, nur mit `diff >= 50` statt `|diff| <= 50`."),
    ("code",
        "summary_stronger = per_player_summary(stronger)\n"
        "table_stronger = cohort.merge(summary_stronger, on='fide_id', how='left')\n"
        "n_cols = [c for c in table_stronger.columns if c.startswith('n_partien') or c.split('_gesamt')[0] in ('siege','remis','niederlagen')]\n"
        "table_stronger[n_cols] = table_stronger[n_cols].fillna(0).astype(int)\n"
        "table_stronger = table_stronger.sort_values('n_partien_gesamt', ascending=False).reset_index(drop=True)\n"
        "table_stronger[display_cols].round(3)"),

    ("md", "### Detailtabelle — stärkere Gegner, getrennt nach Geschlecht"),
    ("code", "table_stronger[detail_cols].round(3)"),

    ("md", "### Aggregat — stärkere Gegner (≥ 50 Elo)"),
    ("code",
        "agg_stronger = pd.DataFrame({\n"
        "    'gesamt': summarize(stronger),\n"
        "    'vs Frauen': summarize(stronger[stronger.opponent_sex == 'F']),\n"
        "    'vs Männer': summarize(stronger[stronger.opponent_sex == 'M']),\n"
        "}).T\n"
        "agg_stronger[['n_partien','siege','remis','niederlagen']] = agg_stronger[['n_partien','siege','remis','niederlagen']].astype(int)\n"
        "agg_stronger.round(4)"),

    ("md", "### Vergleich: gleich stark vs. stärkere Gegner (Aggregat nebeneinander)"),
    ("code",
        "compare = pd.concat({'gleich stark (±50)': agg_equal, 'stärkere Gegner (≥50)': agg_stronger}, axis=0)\n"
        "compare.round(4)"),

    ("md", "## Export"),
    ("code",
        "table_equal.to_csv('top40_equal_strength_summary_per_player.csv', index=False)\n"
        "table_stronger.to_csv('top40_stronger_opponent_summary_per_player.csv', index=False)\n"
        "yearly_equal.to_csv('top40_yearly_avg_opponent_rating_equal.csv')\n"
        "yearly_stronger.to_csv('top40_yearly_avg_opponent_rating_stronger.csv')\n"
        "print('wrote top40_equal_strength_summary_per_player.csv, top40_stronger_opponent_summary_per_player.csv, '\n"
        "      'top40_yearly_avg_opponent_rating_equal.csv, top40_yearly_avg_opponent_rating_stronger.csv')"),
]


if __name__ == "__main__":
    make_notebook(NBDIR / "15_top40_equal_strength_summary.ipynb", nb15)
