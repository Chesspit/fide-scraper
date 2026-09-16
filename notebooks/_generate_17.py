"""Generate notebook 17_top20_2400_strength_breakdown.ipynb.

Top-20-Pendant zu Notebook 16 (dort Top-40) — identischer Aufbau, einziger
fachlicher Unterschied: die Jahresend-Rangschwelle `rnk <= 40` wird zu
`rnk <= 20`.

Run from project root:
    .venv/bin/python notebooks/_generate_17.py
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
    "pd.set_option('display.max_rows', 500)",
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


nb17 = [
    ("md", "# 17 — Stärke-Bucket-Auswertung: Top-20-Frauen im 2400+-Fenster\n\n"
           "**Top-20-Pendant zu Notebook 16** (dort Top-40) — identischer Aufbau, einziger "
           "fachlicher Unterschied: die Jahresend-Rangschwelle ist `rnk <= 20` statt "
           "`rnk <= 40`. Top-20-Frauen nach Jahresend-Elo (`published_rating`, Dezember "
           "2016–2025), gefiltert auf aktuell **aktive** Spielerinnen. Pro Spielerin wird "
           "zusätzlich ihr **Schwellenjahr** bestimmt — das erste Jahr, in dem ihre "
           "Dezember-Elo ≥ 2400 lag — und nur Partien **ab diesem Jahr bis 2025** (permanent, "
           "auch bei späteren Rückgängen unter 2400) fließen in die Analyse ein.\n\n"
           "Jede Partie wird nach **Gegnerstärke zum Zeitpunkt der Partie** in drei Gruppen "
           "eingeteilt (`diff = opponent_rating − own_rating`, `own_rating` = `std_rating` aus "
           "`rating_history` zur jeweiligen Periode — bewusst ein anderes Rating-Konzept als "
           "die `published_rating`-Jahresendwerte, die die Kohorte/Schwelle definieren):\n\n"
           "- **schwächer**: `diff < -50`\n"
           "- **gleich stark**: `-50 <= diff <= 50` (beide Grenzen inklusive)\n"
           "- **stärker**: `diff > 50`\n\n"
           "Dazu ein vierter Bucket **Gesamt**, der die drei Stärke-Gruppen zu einer Zeile "
           "zusammenfasst (alle Partien, unabhängig vom Stärkeverhältnis).\n\n"
           "Jede Gruppe wird zusätzlich nach Gegner-Geschlecht aufgeschlüsselt (Gesamt/vs. "
           "Frauen/vs. Männer). Kennzahlen: Sieg/Remis/Niederlage, Punktequote, "
           "Ø-Gegner-Elo, Ø-Elo-Änderung (`rating_change_weighted`), Anzahl Partien.\n\n"
           "**Datenarchitektur:** wie Notebook 16 — ein einziger Partien-Faktentensor "
           "(`games`) trägt alle nötigen Spalten (`name`, `year`, `strength_bucket`, "
           "`opponent_sex`); eine generische `summarize()`/`build_table()`-Kombination "
           "erzeugt daraus je nach übergebenen Gruppierungsspalten alle Tabellen unten, "
           "auch der `Gesamt`-Bucket entsteht per Rollup statt als Extra-Codepfad."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Kohorte + Schwellenjahr laden\n\n"
           "Eine Query liefert für jede Spielerin, die je in einem Top-20-Jahresende stand, "
           "ihre komplette Jahresend-Historie 2016–2025 (Rang + Rating) — daraus werden in "
           "Pandas `year_added` (erstes Top-20-Jahr) und `threshold_year` (erstes Jahr mit "
           "`published_rating >= 2400`) abgeleitet und auf `active = TRUE` gefiltert."),
    ("code",
        "sql_roster = '''\n"
        "WITH female_years AS (\n"
        "    SELECT rh.fide_id, p.name, p.active, rh.period, rh.published_rating,\n"
        "           ROW_NUMBER() OVER (PARTITION BY rh.period ORDER BY rh.published_rating DESC) AS rnk\n"
        "    FROM rating_history rh\n"
        "    JOIN players p ON p.fide_id = rh.fide_id\n"
        "    WHERE p.sex = 'F'\n"
        "      AND rh.published_rating IS NOT NULL\n"
        "      AND rh.period IN ('2016-12-01','2017-12-01','2018-12-01','2019-12-01','2020-12-01',\n"
        "                        '2021-12-01','2022-12-01','2023-12-01','2024-12-01','2025-12-01')\n"
        "),\n"
        "top20_ids AS (\n"
        "    SELECT DISTINCT fide_id FROM female_years WHERE rnk <= 20\n"
        ")\n"
        "SELECT fy.fide_id, fy.name, fy.active, fy.period, fy.published_rating, fy.rnk\n"
        "FROM female_years fy\n"
        "JOIN top20_ids t ON t.fide_id = fy.fide_id\n"
        "'''\n"
        "raw = load_query(sql_roster)\n"
        "raw['year'] = pd.to_datetime(raw['period']).dt.year\n"
        "raw_active = raw[raw['active']].copy()\n"
        "\n"
        "year_added = raw_active[raw_active['rnk'] <= 20].groupby('fide_id')['year'].min().rename('year_added')\n"
        "threshold_year = raw_active[raw_active['published_rating'] >= 2400].groupby('fide_id')['year'].min().rename('threshold_year')\n"
        "names = raw_active.drop_duplicates('fide_id').set_index('fide_id')['name']\n"
        "\n"
        "roster = pd.concat([names, year_added, threshold_year], axis=1).reset_index()\n"
        "assert roster['threshold_year'].notna().all(), 'Spielerin ohne Schwellenjahr gefunden'\n"
        "roster['year_added'] = roster['year_added'].astype(int)\n"
        "roster['threshold_year'] = roster['threshold_year'].astype(int)\n"
        "roster = roster.sort_values('name').reset_index(drop=True)\n"
        "\n"
        "print(f'Kohorte: {len(roster)} Spielerinnen')\n"
        "print('Schwellenjahr-Verteilung:')\n"
        "print(roster['threshold_year'].value_counts().sort_index())"),

    ("md", "## Partien-Fakten-Tabelle laden\n\n"
           "Pro Spielerin nur Partien ab ihrem individuellen Schwellenjahr (`VALUES`-Liste aus "
           "`roster`, ein Fenster pro Spielerin statt eines einzigen globalen Zeitraums). "
           "`own_rating` wie in Notebook 13–16 per `LEFT JOIN rating_history` auf `std_rating` "
           "(Rating zum Partie-Zeitpunkt, nicht der Jahresend-Snapshot)."),
    ("code",
        "values_rows = ',\\n    '.join(\n"
        "    f\"({int(fid)}, DATE '{int(ty)}-01-01')\"\n"
        "    for fid, ty in zip(roster['fide_id'], roster['threshold_year'])\n"
        ")\n"
        "sql_games = f'''\n"
        "WITH windows(fide_id, start_date) AS (\n"
        "    VALUES\n"
        "    {values_rows}\n"
        ")\n"
        "SELECT gr.fide_id, gr.period, gr.opponent_rating, gr.opponent_sex,\n"
        "       gr.result, gr.rating_change_weighted, rh.std_rating AS own_rating\n"
        "FROM game_results gr\n"
        "JOIN windows w             ON w.fide_id = gr.fide_id\n"
        "LEFT JOIN rating_history rh ON rh.fide_id = gr.fide_id AND rh.period = gr.period\n"
        "WHERE gr.period >= w.start_date AND gr.period <= '2025-12-31'\n"
        "'''\n"
        "games_raw = load_query(sql_games)\n"
        "games_raw = games_raw.merge(roster[['fide_id', 'name']], on='fide_id', how='left')\n"
        "\n"
        "games_raw['result'] = games_raw['result'].astype(float)\n"
        "games_raw['rating_change_weighted'] = games_raw['rating_change_weighted'].astype(float)\n"
        "games_raw['own_rating'] = pd.to_numeric(games_raw['own_rating'], errors='coerce')\n"
        "games_raw['opponent_rating'] = pd.to_numeric(games_raw['opponent_rating'], errors='coerce')\n"
        "games_raw['year'] = pd.to_datetime(games_raw['period']).dt.year\n"
        "\n"
        "n_raw = len(games_raw)\n"
        "games = games_raw.dropna(subset=['own_rating', 'opponent_rating']).copy()\n"
        "print(f'{n_raw:,} Partien geladen, {n_raw - len(games):,} ohne own_rating/opponent_rating verworfen '\n"
        "      f'({(n_raw - len(games)) / n_raw:.1%}) -> {len(games):,} Partien in der Analyse')\n"
        "print(f'Eindeutige Spielerinnen mit Partien: {games[\"fide_id\"].nunique()}')\n"
        "\n"
        "games['diff'] = games['opponent_rating'] - games['own_rating']\n"
        "\n"
        "def strength_bucket(d):\n"
        "    if pd.isna(d):\n"
        "        return 'unknown'\n"
        "    if d > 50:\n"
        "        return 'stärker'\n"
        "    if d < -50:\n"
        "        return 'schwächer'\n"
        "    return 'gleich'\n"
        "games['strength_bucket'] = games['diff'].apply(strength_bucket)\n"
        "\n"
        "BUCKET_ORDER = ['schwächer', 'gleich', 'stärker']\n"
        "BUCKET_ORDER_FULL = BUCKET_ORDER + ['Gesamt']\n"
        "SEX_GROUP_ORDER = ['Gesamt', 'F', 'M']\n"
        "N_COLS = ['n_partien', 'siege', 'remis', 'niederlagen']\n"
        "games['strength_bucket'].value_counts().reindex(BUCKET_ORDER)"),

    ("md", "## 1. Partien pro Jahr × Spielerin"),
    ("code",
        "year_pivot = games.groupby(['name', 'year']).size().unstack(fill_value=0)\n"
        "for y in range(2016, 2026):\n"
        "    if y not in year_pivot.columns:\n"
        "        year_pivot[y] = 0\n"
        "year_pivot = year_pivot[sorted(year_pivot.columns)]\n"
        "year_pivot['Gesamt'] = year_pivot.sum(axis=1)\n"
        "year_pivot = year_pivot.sort_values('Gesamt', ascending=False)\n"
        "year_pivot"),

    ("md", "## 2. Kennzahlen-Helfer\n\n"
           "`summarize()` liefert für eine beliebige Partien-Teilmenge Sieg/Remis/Niederlage, "
           "Punktequote, Ø-Gegner-Elo und Ø-Elo-Änderung. `with_bucket_rollup()` verdoppelt den "
           "DataFrame um eine zusätzliche `strength_bucket == 'Gesamt'`-Kopie (Summe über "
           "schwächer/gleich/stärker); `with_sex_rollup()` verdreifacht zusätzlich nach "
           "Gegner-Geschlecht (Gesamt/vs. F/vs. M als Spalte `sex_group`). Beide zusammen "
           "liefern in einem einzigen `groupby` sowohl die volle Stärke- als auch die "
           "Geschlechts-Aufschlüsselung inkl. Gesamtzeilen. `build_table()` gruppiert nach "
           "beliebigen Spalten — dieselbe Funktion erzeugt alle Tabellen unten, nur mit "
           "anderen Gruppierungsspalten."),
    ("code",
        "def summarize(sub):\n"
        "    n = len(sub)\n"
        "    if n == 0:\n"
        "        return pd.Series({\n"
        "            'n_partien': 0, 'siege': 0, 'remis': 0, 'niederlagen': 0,\n"
        "            'punktequote': np.nan, 'avg_opponent_rating': np.nan, 'mean_delta': np.nan,\n"
        "        })\n"
        "    return pd.Series({\n"
        "        'n_partien': n,\n"
        "        'siege': (sub['result'] == 1).sum(),\n"
        "        'remis': (sub['result'] == 0.5).sum(),\n"
        "        'niederlagen': (sub['result'] == 0).sum(),\n"
        "        'punktequote': sub['result'].mean(),\n"
        "        'avg_opponent_rating': sub['opponent_rating'].mean(),\n"
        "        'mean_delta': sub['rating_change_weighted'].mean(),\n"
        "    })\n"
        "\n"
        "def with_sex_rollup(df_scope):\n"
        "    return pd.concat([\n"
        "        df_scope.assign(sex_group='Gesamt'),\n"
        "        df_scope[df_scope.opponent_sex == 'F'].assign(sex_group='F'),\n"
        "        df_scope[df_scope.opponent_sex == 'M'].assign(sex_group='M'),\n"
        "    ], ignore_index=True)\n"
        "\n"
        "def with_bucket_rollup(df_scope):\n"
        "    return pd.concat([\n"
        "        df_scope,\n"
        "        df_scope.assign(strength_bucket='Gesamt'),\n"
        "    ], ignore_index=True)\n"
        "\n"
        "def build_table(df_scope, group_cols):\n"
        "    return df_scope.groupby(group_cols).apply(summarize, include_groups=False).reset_index()\n"
        "\n"
        "games_full = with_sex_rollup(with_bucket_rollup(games))\n"
        "print(f'games_full: {len(games_full):,} Zeilen (Original × 4 Stärke-Buckets [inkl. Gesamt] × Gesamt/F/M-Rollup)')"),

    ("md", "## 3. Gesamtzeitraum — pro Spielerin × Bucket × Geschlecht\n\n"
           "Vollständiges Raster (Spielerinnen × 4 Buckets [schwächer/gleich/stärker + "
           "**Gesamt** als Summe der drei] × 3 Geschlechts-Gruppen); Kombinationen ohne "
           "Partien erscheinen mit 0."),
    ("code",
        "full_index_overall_player = pd.MultiIndex.from_product(\n"
        "    [roster['name'], BUCKET_ORDER_FULL, SEX_GROUP_ORDER],\n"
        "    names=['name', 'strength_bucket', 'sex_group'],\n"
        ")\n"
        "table_overall = (\n"
        "    build_table(games_full, ['name', 'strength_bucket', 'sex_group'])\n"
        "    .set_index(['name', 'strength_bucket', 'sex_group'])\n"
        "    .reindex(full_index_overall_player)\n"
        ")\n"
        "table_overall[N_COLS] = table_overall[N_COLS].fillna(0).astype(int)\n"
        "table_overall = table_overall.reset_index()\n"
        "table_overall.round(3)"),

    ("md", "## 4. Gesamtzeitraum — aggregiert über alle Spielerinnen\n\n"
           "Die \"Executive Summary\"-Tabelle: 4 Buckets (schwächer/gleich/stärker + **Gesamt** "
           "als Summe der drei Stärke-Gruppen) × 3 Geschlechts-Gruppen = 12 Zeilen. Die "
           "`Gesamt`-Bucket-Zeilen beantworten dabei auch die stärke-unabhängige Frage "
           "\"schneiden die Top-20-Frauen gegen Männer anders ab als gegen Frauen, über alle "
           "Partien hinweg?\" — eine separate Tabelle ohne Stärke-Split ist dafür nicht mehr "
           "nötig."),
    ("code",
        "full_index_overall = pd.MultiIndex.from_product(\n"
        "    [BUCKET_ORDER_FULL, SEX_GROUP_ORDER], names=['strength_bucket', 'sex_group']\n"
        ")\n"
        "agg_overall = (\n"
        "    build_table(games_full, ['strength_bucket', 'sex_group'])\n"
        "    .set_index(['strength_bucket', 'sex_group'])\n"
        "    .reindex(full_index_overall)\n"
        ")\n"
        "agg_overall[N_COLS] = agg_overall[N_COLS].fillna(0).astype(int)\n"
        "agg_overall = agg_overall.reset_index()\n"
        "agg_overall.round(4)"),

    ("md", "## 5. Chart — Punktequote je Bucket × Gegner-Geschlecht (Gesamtzeitraum)"),
    ("code",
        "plot_data = agg_overall[agg_overall.sex_group.isin(['F', 'M'])]\n"
        "fig, ax = plt.subplots(figsize=(9, 5))\n"
        "sns.barplot(\n"
        "    data=plot_data, x='strength_bucket', order=BUCKET_ORDER_FULL,\n"
        "    y='punktequote', hue='sex_group', hue_order=['F', 'M'],\n"
        "    palette={'F': '#c0587e', 'M': '#4a7ab5'}, ax=ax,\n"
        ")\n"
        "ax.axhline(0.5, color='grey', lw=1, ls='--')\n"
        "ax.set_xlabel('Gegnerstärke-Bucket (± 50 Elo, \"Gesamt\" = alle drei zusammen)')\n"
        "ax.set_ylabel('Punktequote')\n"
        "ax.set_title('Punktequote je Stärke-Bucket × Gegner-Geschlecht, Gesamtzeitraum (2400+-Fenster)')\n"
        "ax.legend(title='Gegner')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 6. Pro Jahr — aggregiert über alle Spielerinnen"),
    ("code",
        "years_present = sorted(games['year'].unique())\n"
        "full_index_year = pd.MultiIndex.from_product(\n"
        "    [years_present, BUCKET_ORDER_FULL, SEX_GROUP_ORDER],\n"
        "    names=['year', 'strength_bucket', 'sex_group'],\n"
        ")\n"
        "agg_by_year = (\n"
        "    build_table(games_full, ['year', 'strength_bucket', 'sex_group'])\n"
        "    .set_index(['year', 'strength_bucket', 'sex_group'])\n"
        "    .reindex(full_index_year)\n"
        ")\n"
        "agg_by_year[N_COLS] = agg_by_year[N_COLS].fillna(0).astype(int)\n"
        "agg_by_year = agg_by_year.reset_index()\n"
        "agg_by_year.round(4)"),

    ("code",
        "plot_year = agg_by_year[agg_by_year.sex_group == 'Gesamt']\n"
        "fig, ax = plt.subplots(figsize=(10, 5))\n"
        "for bucket, color in zip(BUCKET_ORDER_FULL, ['#d7191c', '#4a7ab5', '#2a9d3f', '#888888']):\n"
        "    sub = plot_year[plot_year.strength_bucket == bucket]\n"
        "    ax.plot(sub['year'], sub['punktequote'], marker='o', label=bucket, color=color)\n"
        "ax.axhline(0.5, color='grey', lw=1, ls='--')\n"
        "ax.set_xlabel('Jahr')\n"
        "ax.set_ylabel('Punktequote')\n"
        "ax.set_title('Punktequote je Jahr × Stärke-Bucket (alle Gegner, aggregiert über alle Spielerinnen)')\n"
        "ax.legend(title='Bucket')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 7. Pro Jahr × Spielerin (Datenwürfel, nur Export)\n\n"
           "Wie bei Notebook 16 wird hier **nicht** auf ein volles Raster reindexiert: Jahre vor "
           "dem individuellen Schwellenjahr einer Spielerin fehlen zu Recht (sie waren dort noch "
           "nicht im 2400+-Fenster), eine erzwungene 0-Zeile wäre irreführend."),
    ("code",
        "table_by_year = build_table(games_full, ['name', 'year', 'strength_bucket', 'sex_group'])\n"
        "table_by_year[N_COLS] = table_by_year[N_COLS].astype(int)\n"
        "table_by_year['strength_bucket'] = pd.Categorical(table_by_year['strength_bucket'], BUCKET_ORDER_FULL, ordered=True)\n"
        "table_by_year['sex_group'] = pd.Categorical(table_by_year['sex_group'], SEX_GROUP_ORDER, ordered=True)\n"
        "table_by_year = table_by_year.sort_values(['name', 'year', 'strength_bucket', 'sex_group']).reset_index(drop=True)\n"
        "print(f'{len(table_by_year):,} Zeilen im vollen Datenwürfel — Beispiel für eine Spielerin:')\n"
        "\n"
        "demo_name = roster.loc[roster['threshold_year'] == roster['threshold_year'].min(), 'name'].iloc[0]\n"
        "table_by_year[table_by_year.name == demo_name].round(3)"),

    ("md", "## 8. Signifikanztest — gleich starke Gegner, Frauen vs. Männer (Spieler-Ebene)\n\n"
           "Wie in Notebook 16: Permutationstest auf **Spieler-Ebene** (Ø Punktequote pro "
           "`fide_id`, nicht pro Partie), um den Cluster-Effekt durch stark ungleiche "
           "Partienzahlen pro Person zu vermeiden. 10.000 Permutationen, fester Seed, kein "
           "scipy/statsmodels nötig."),
    ("code",
        "def permutation_test(a, b, n_perm=10000, seed=42, return_diffs=False):\n"
        "    rng = np.random.default_rng(seed)\n"
        "    observed = a.mean() - b.mean()\n"
        "    pooled = np.concatenate([a, b])\n"
        "    n_a = len(a)\n"
        "    diffs = np.empty(n_perm)\n"
        "    for i in range(n_perm):\n"
        "        rng.shuffle(pooled)\n"
        "        diffs[i] = pooled[:n_a].mean() - pooled[n_a:].mean()\n"
        "    p_value = (np.abs(diffs) >= np.abs(observed)).mean()\n"
        "    return (observed, p_value, diffs) if return_diffs else (observed, p_value)\n"
        "\n"
        "gleich = games[games.strength_bucket == 'gleich']\n"
        "pm_f = gleich[gleich.opponent_sex == 'F'].groupby('fide_id')['result'].mean()\n"
        "pm_m = gleich[gleich.opponent_sex == 'M'].groupby('fide_id')['result'].mean()\n"
        "a, b = pm_f.values, pm_m.values\n"
        "observed, p_value, diffs = permutation_test(a, b, return_diffs=True)\n"
        "\n"
        "print(f'Spielerinnen mit Partien vs. Frauen (gleich stark): {len(a)}')\n"
        "print(f'Spielerinnen mit Partien vs. Männer (gleich stark): {len(b)}')\n"
        "print(f'Ø Punktequote vs. Frauen (Spieler-Mittel): {a.mean():.4f}')\n"
        "print(f'Ø Punktequote vs. Männer (Spieler-Mittel): {b.mean():.4f}')\n"
        "print(f'Beobachtete Differenz (Frauen \\u2212 Männer): {observed:+.4f}')\n"
        "print(f'Permutations-p-Wert (10.000 Permutationen, zweiseitig): {p_value:.4f}')\n"
        "if min(len(a), len(b)) < 8:\n"
        "    print('Achtung: underpowered (< 8 Spielerinnen in einer Gruppe)')"),

    ("code",
        "fig, ax = plt.subplots()\n"
        "ax.hist(diffs, bins=50, color='#888888', alpha=0.8)\n"
        "ax.axvline(observed, color='#c0587e', lw=2,\n"
        "           label=f'beobachtet ({observed:+.3f}), p={p_value:.4f}')\n"
        "ax.axvline(0, color='grey', lw=1, ls='--')\n"
        "ax.set_title('Permutations-Nullverteilung: gleich starke Gegner, Frauen vs. Männer (Spieler-Ebene)')\n"
        "ax.set_xlabel('Differenz der Spieler-Mittelwerte der Punktequote (vs. Frauen \\u2212 vs. Männer)')\n"
        "ax.legend()\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "### Robustheits-Check: Mindestanzahl Partien pro Spielerin\n\n"
           "Bei `min_n=1` zählt eine Spielerin mit nur 1–4 Partien vs. Männer genauso viel wie "
           "eine mit vielen — ihr persönlicher Mittelwert ist dann extrem verrauscht. Sweep über "
           "eine Mindest-Partienzahl pro Spielerin prüft, ob der Effekt robust ist oder von genau "
           "diesen dünn besetzten Zellen getrieben wird."),
    ("code",
        "pm_f_full = gleich[gleich.opponent_sex == 'F'].groupby('fide_id')['result'].agg(['mean', 'count'])\n"
        "pm_m_full = gleich[gleich.opponent_sex == 'M'].groupby('fide_id')['result'].agg(['mean', 'count'])\n"
        "\n"
        "sweep_rows = []\n"
        "for min_n in [1, 5, 8, 10]:\n"
        "    a_s = pm_f_full[pm_f_full['count'] >= min_n]['mean'].values\n"
        "    b_s = pm_m_full[pm_m_full['count'] >= min_n]['mean'].values\n"
        "    obs_s, p_s = permutation_test(a_s, b_s)\n"
        "    sweep_rows.append({\n"
        "        'min_partien_pro_spielerin': min_n, 'n_spielerinnen_f': len(a_s), 'n_spielerinnen_m': len(b_s),\n"
        "        'mean_f': round(a_s.mean(), 4), 'mean_m': round(b_s.mean(), 4),\n"
        "        'differenz': round(obs_s, 4), 'p_wert': round(p_s, 4),\n"
        "    })\n"
        "pd.DataFrame(sweep_rows)"),

    ("md", "## Export\n\n"
           "CSVs landen (wie bei Notebook 16) relativ im Ausführungsverzeichnis "
           "(`notebooks/`, per `.gitignore` ausgeschlossen). `top20_2400_overall_by_sex.csv` "
           "wird direkt aus der `Gesamt`-Bucket-Zeile von Tabelle 4 abgeleitet (kein separater "
           "Rechenweg mehr nötig, siehe Abschnitt 4)."),
    ("code",
        "agg_overall_no_bucket = (\n"
        "    agg_overall[agg_overall.strength_bucket == 'Gesamt']\n"
        "    .drop(columns='strength_bucket')\n"
        "    .set_index('sex_group')\n"
        "    .reindex(SEX_GROUP_ORDER)\n"
        "    .reset_index()\n"
        ")\n"
        "\n"
        "year_pivot.to_csv('top20_2400_games_per_year.csv')\n"
        "table_overall.to_csv('top20_2400_strength_summary_overall_per_player.csv', index=False)\n"
        "agg_overall.to_csv('top20_2400_strength_summary_overall_aggregate.csv', index=False)\n"
        "agg_overall_no_bucket.to_csv('top20_2400_overall_by_sex.csv', index=False)\n"
        "agg_by_year.to_csv('top20_2400_strength_summary_by_year_aggregate.csv', index=False)\n"
        "table_by_year.to_csv('top20_2400_strength_summary_by_year_per_player.csv', index=False)\n"
        "print('wrote 6 CSVs: top20_2400_games_per_year.csv, top20_2400_strength_summary_overall_per_player.csv, '\n"
        "      'top20_2400_strength_summary_overall_aggregate.csv, top20_2400_overall_by_sex.csv, '\n"
        "      'top20_2400_strength_summary_by_year_aggregate.csv, top20_2400_strength_summary_by_year_per_player.csv')"),

    ("md", "## Fazit\n\n"
           "Tabelle 4 (Abschnitt 4) beantwortet die Kernfrage direkt: schneiden die "
           "Top-20-Frauen im 2400+-Fenster gegen gleich starke/stärkere/schwächere Männer "
           "anders ab als gegen ebenso eingestufte Frauen? Die zusätzliche `Gesamt`-Bucket-Zeile "
           "zeigt denselben Vergleich ohne die Stärke-Differenzierung — nur noch Gesamt/vs. "
           "Frauen/vs. Männer über alle Partien hinweg, unabhängig vom relativen "
           "Stärkeverhältnis. Tabelle 3 (pro Spielerin) und der "
           "Jahres-Export (Abschnitt 7) erlauben das Aufbrechen dieser Frage auf Einzelspieler- "
           "bzw. Jahresebene. Der Permutationstest (Abschnitt 8) prüft für die sauberste "
           "Vergleichszelle — gleich starke Gegner —, ob ein Punktequote-Unterschied zwischen "
           "Frauen- und Männer-Gegnern über Stichprobenrauschen hinausgeht.\n\n"
           "**Caveats (wie Notebook 16):**\n"
           "- Bucket-Grenzen: `gleich` schließt exakt ±50 Elo ein; `schwächer`/`stärker` "
           "beginnen erst echt darüber/darunter; `Gesamt` ist die reine Summe der drei "
           "(keine eigene Stärke-Definition).\n"
           "- `own_rating` = `std_rating` zum Partie-Zeitpunkt — ein anderes Rating-Konzept als "
           "die `published_rating`-Jahresendwerte, die Kohorte und Schwellenjahr definieren; "
           "beide Konzepte sind bewusst getrennt gehalten.\n"
           "- Das Schwellenjahr-Fenster ist pro Spielerin unterschiedlich lang — "
           "Aggregat-Tabellen über alle Spielerinnen sind entsprechend nicht gleichgewichtig "
           "über die Karrierephasen verteilt.\n"
           "- Die Top-20-Kohorte ist eine echte Teilmenge der Top-40-Kohorte aus Notebook 16 "
           "(jede Top-20-Spielerin war in mindestens einem Jahr auch Top-40) — entsprechend "
           "kleinere Fallzahlen pro Zelle, siehe Abschnitt 1."),
]


if __name__ == "__main__":
    make_notebook(NBDIR / "17_top20_2400_strength_breakdown.ipynb", nb17)
