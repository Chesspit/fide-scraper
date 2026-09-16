"""Generate notebook 14_top40_female_vs_band_men.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_14.py
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
    "pd.set_option('display.max_rows', 250)",
    "",
    "COHORT_ORDER = ['top40_female', 'male_2400_2600']",
    "COHORT_PALETTE = {'top40_female': '#c0587e', 'male_2400_2600': '#4a7ab5'}",
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


nb14 = [
    ("md", "# 14 — Top-40-Frauen (Jahresende 2016–2025) vs. Männer im Elo-Band 2400–2600\n\n"
           "**Nachfolger von Notebook 13.** Notebook 13 verglich die statischen Gruppen "
           "`female_top`/`male_control` (`players.analysis_group`) — die sich beim Testlauf als "
           "nur unvollständig befüllt herausstellten (`groups.backfill_status='partial'`, nur "
           "23/66 bzw. 48/649 Spieler gelabelt, kaum aktive). Dieses Notebook ersetzt die "
           "Kohorten-Definition komplett durch eine **dynamische, survivorship-bias-freie** "
           "Ableitung aus `rating_history.published_rating` (siehe "
           "`docs/ideen_verbesserungen.md`, Abschnitte F2/F7) — keine statischen Labels, keine "
           "Pflege, kein Scraping-Backfill nötig.\n\n"
           "**Kohorten:**\n"
           "- **`top40_female`:** alle Frauen, die an mindestens einem der Jahresenden "
           "Dez-2016 … Dez-2025 zu den Top 40 nach `published_rating` gehörten (Union über "
           "10 Jahre → 65 Spielerinnen, Cutoff-Rating je Jahr ~2448–2460).\n"
           "- **`male_2400_2600`:** alle Männer, deren `std_rating` zum Zeitpunkt einer Partie "
           "(2016–2025) im Band 2400–2600 lag — eine breite Vergleichspopulation im selben "
           "Elo-Band, keine Top-40-Beschränkung (sonst wäre das die absolute Weltspitze ab "
           "~2650, ein anderes Rating-Niveau).\n\n"
           "**Zeitfenster:** nur Partien 2016–2025 (Zeitraum, für den die Kohorte definiert "
           "wurde) — nicht die gesamte Karriere.\n\n"
           "**Definitionen (wie Notebook 13):** Elo-Band = 50-Punkte-Bänder auf `own_rating`; "
           "Stärke-Bucket = ±50 Elo relativ zum Gegner; Scope = `open_mixed` vs. `women_only` "
           "(`tournament_type ∈ {women, women_team}`), nicht gefiltert sondern parallel "
           "ausgewiesen."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Datenbasis laden\n\n"
           "Zwei Teilabfragen: die Frauen-Kohorte wird per Rang (Top 40 je Jahresende) "
           "bestimmt, die Männer-Population per Elo-Band zum Partie-Zeitpunkt — beides direkt "
           "aus `rating_history`, ohne `analysis_group`."),
    ("code",
        "sql = '''\n"
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
        "    gr.fide_id, 'top40_female' AS cohort,\n"
        "    rh.std_rating AS own_rating, gr.opponent_rating, gr.opponent_sex, gr.result,\n"
        "    gr.rating_change_weighted, gr.expected_score, gr.over_performance, gr.tournament_type\n"
        "FROM game_results gr\n"
        "JOIN female_cohort fc        ON fc.fide_id = gr.fide_id\n"
        "LEFT JOIN rating_history rh  ON rh.fide_id = gr.fide_id AND rh.period = gr.period\n"
        "WHERE gr.period BETWEEN '2016-01-01' AND '2025-12-31'\n"
        "  AND gr.opponent_sex IN ('M', 'F')\n"
        "\n"
        "UNION ALL\n"
        "\n"
        "SELECT\n"
        "    gr.fide_id, 'male_2400_2600' AS cohort,\n"
        "    rh.std_rating AS own_rating, gr.opponent_rating, gr.opponent_sex, gr.result,\n"
        "    gr.rating_change_weighted, gr.expected_score, gr.over_performance, gr.tournament_type\n"
        "FROM game_results gr\n"
        "JOIN players p               ON p.fide_id = gr.fide_id\n"
        "JOIN rating_history rh       ON rh.fide_id = gr.fide_id AND rh.period = gr.period\n"
        "WHERE p.sex = 'M'\n"
        "  AND rh.std_rating BETWEEN 2400 AND 2600\n"
        "  AND gr.period BETWEEN '2016-01-01' AND '2025-12-31'\n"
        "  AND gr.opponent_sex IN ('M', 'F')\n"
        "'''\n"
        "raw = load_query(sql)\n"
        "n_raw = len(raw)\n"
        "df = raw.dropna(subset=['own_rating', 'opponent_rating']).copy()\n"
        "print(f'{n_raw:,} Partien geladen, {n_raw - len(df):,} ohne own_rating/opponent_rating verworfen '\n"
        "      f'({(n_raw - len(df)) / n_raw:.1%}) -> {len(df):,} Partien in der Analyse')\n"
        "\n"
        "df['result'] = df['result'].astype(float)\n"
        "df['rating_change_weighted'] = df['rating_change_weighted'].astype(float)\n"
        "df['expected_score'] = df['expected_score'].astype(float)\n"
        "df['over_performance'] = df['over_performance'].astype(float)\n"
        "df['own_rating'] = pd.to_numeric(df['own_rating'], errors='coerce')\n"
        "df['opponent_rating'] = pd.to_numeric(df['opponent_rating'], errors='coerce')\n"
        "df['diff'] = df['opponent_rating'] - df['own_rating']\n"
        "\n"
        "def elo_band(r):\n"
        "    if pd.isna(r):\n"
        "        return 'unknown'\n"
        "    lo = int(r // 50) * 50\n"
        "    return f'{lo}-{lo + 49}'\n"
        "df['elo_band'] = df['own_rating'].apply(elo_band)\n"
        "\n"
        "def strength_bucket(d):\n"
        "    if pd.isna(d):\n"
        "        return 'unknown'\n"
        "    if d > 50:\n"
        "        return 'stärker'\n"
        "    if d < -50:\n"
        "        return 'schwächer'\n"
        "    return 'gleich'\n"
        "df['strength'] = df['diff'].apply(strength_bucket)\n"
        "\n"
        "df['scope'] = df['tournament_type'].apply(\n"
        "    lambda t: 'women_only' if t in ('women', 'women_team') else 'open_mixed'\n"
        ")\n"
        "\n"
        "def band_sort_key(b):\n"
        "    return (9999,) if b == 'unknown' else (int(b.split('-')[0]),)\n"
        "elo_band_order = sorted(df['elo_band'].unique(), key=band_sort_key)\n"
        "\n"
        "SEX_ORDER = ['F', 'M']\n"
        "STRENGTH_ORDER = ['stärker', 'gleich', 'schwächer']\n"
        "print('Elo-Bänder:', elo_band_order)\n"
        "df.head()"),

    ("md", "## 1. Zellgrößen (Plausibilitätscheck)\n\n"
           "Die Frauen-Kohorte bleibt mit 65 Spielerinnen klein — Zellen an den Rändern der "
           "Elo-Range können dünn besetzt sein. Die Männer-Population ist deutlich größer "
           "(587 Spieler)."),
    ("code",
        "qc = (\n"
        "    df.groupby(['cohort', 'elo_band'])\n"
        "      .agg(n_games=('fide_id', 'size'), n_players=('fide_id', 'nunique'))\n"
        "      .reset_index()\n"
        ")\n"
        "qc.pivot(index='elo_band', columns='cohort', values=['n_games', 'n_players']).reindex(elo_band_order)"),

    ("md", "## Helper: Metrik-Tabellen"),
    ("code",
        "def build_metrics(df_scope):\n"
        "    g = df_scope.groupby(['elo_band', 'opponent_sex', 'cohort'])\n"
        "    return g.agg(\n"
        "        n_games=('fide_id', 'size'),\n"
        "        n_players=('fide_id', 'nunique'),\n"
        "        score_rate=('result', 'mean'),\n"
        "        mean_expected_score=('expected_score', 'mean'),\n"
        "        mean_over_performance=('over_performance', 'mean'),\n"
        "        sum_rating_change_weighted=('rating_change_weighted', 'sum'),\n"
        "        mean_rating_change_weighted=('rating_change_weighted', 'mean'),\n"
        "    ).reset_index()\n"
        "\n"
        "def pivot_metric(metrics_df, value_col, round_to=4):\n"
        "    tbl = metrics_df.pivot(index='elo_band', columns=['opponent_sex', 'cohort'], values=value_col)\n"
        "    tbl = tbl.reindex(elo_band_order)\n"
        "    cols = [(s, c) for s in SEX_ORDER for c in COHORT_ORDER if (s, c) in tbl.columns]\n"
        "    return tbl[cols].round(round_to)\n"
        "\n"
        "metrics_all = build_metrics(df)\n"
        "metrics_open = build_metrics(df[df.scope == 'open_mixed'])\n"
        "print(f'Zellen gesamt: {len(metrics_all)}   Zellen (nur offene Turniere): {len(metrics_open)}')"),

    ("md", "## 2. Haupttabelle — Ø Over-Performance je Elo-Band × Gegner-Geschlecht × Kohorte\n\n"
           "`over_performance = result − expected_score` (Elo-Erwartung). Positiv = besser als "
           "erwartet.\n\n"
           "**Alle Partien:**"),
    ("code", "pivot_metric(metrics_all, 'mean_over_performance')"),

    ("md", "**Nur offene/gemischte Turniere** (ohne `women`/`women_team`):"),
    ("code", "pivot_metric(metrics_open, 'mean_over_performance')"),

    ("md", "### Zum Vergleich: Score-Rate und Ø rating_change_weighted (alle Partien)"),
    ("code", "pivot_metric(metrics_all, 'score_rate')"),
    ("code", "pivot_metric(metrics_all, 'mean_rating_change_weighted')"),

    ("md", "## 3. Heatmap: Ø Over-Performance nach Elo-Band × Gegner-Geschlecht"),
    ("code",
        "fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)\n"
        "for ax, coh in zip(axes, COHORT_ORDER):\n"
        "    sub = (\n"
        "        metrics_all[metrics_all.cohort == coh]\n"
        "        .pivot(index='elo_band', columns='opponent_sex', values='mean_over_performance')\n"
        "        .reindex(index=elo_band_order, columns=SEX_ORDER)\n"
        "    )\n"
        "    sns.heatmap(sub, annot=True, fmt='.3f', cmap='RdBu_r', center=0, ax=ax, cbar=True)\n"
        "    ax.set_title(coh)\n"
        "    ax.set_xlabel('Gegner-Geschlecht')\n"
        "    ax.set_ylabel('Elo-Band (eigenes Rating)')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 4. Balkendiagramm: Ø rating_change_weighted je Elo-Band, Kohorte im Vergleich"),
    ("code",
        "def bar_chart(metrics_df, title_suffix):\n"
        "    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)\n"
        "    for ax, sex in zip(axes, SEX_ORDER):\n"
        "        sub = metrics_df[metrics_df.opponent_sex == sex]\n"
        "        pv = (\n"
        "            sub.pivot(index='elo_band', columns='cohort', values='mean_rating_change_weighted')\n"
        "            .reindex(index=elo_band_order, columns=COHORT_ORDER)\n"
        "        )\n"
        "        pv.plot.bar(ax=ax, color=[COHORT_PALETTE[c] for c in COHORT_ORDER], edgecolor='white')\n"
        "        ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "        ax.set_title(f'vs {sex} ({title_suffix})')\n"
        "        ax.set_xlabel('Elo-Band (eigenes Rating)')\n"
        "        ax.set_ylabel('Ø rating_change_weighted')\n"
        "        ax.tick_params(axis='x', rotation=45)\n"
        "    plt.tight_layout(); plt.show()\n"
        "\n"
        "bar_chart(metrics_all, 'alle Partien')"),
    ("code", "bar_chart(metrics_open, 'nur offene Turniere')"),

    ("md", "## 5. Sekundärachse: relative Gegnerstärke (±50 Elo)"),
    ("code",
        "def build_strength_metrics(df_scope):\n"
        "    g = df_scope.groupby(['strength', 'opponent_sex', 'cohort'])\n"
        "    return g.agg(\n"
        "        n_games=('fide_id', 'size'),\n"
        "        n_players=('fide_id', 'nunique'),\n"
        "        score_rate=('result', 'mean'),\n"
        "        mean_over_performance=('over_performance', 'mean'),\n"
        "        mean_rating_change_weighted=('rating_change_weighted', 'mean'),\n"
        "    ).reset_index()\n"
        "\n"
        "strength_metrics = build_strength_metrics(df)\n"
        "tbl = strength_metrics.pivot(index='strength', columns=['opponent_sex', 'cohort'], values='mean_over_performance')\n"
        "tbl = tbl.reindex(STRENGTH_ORDER)\n"
        "cols = [(s, c) for s in SEX_ORDER for c in COHORT_ORDER if (s, c) in tbl.columns]\n"
        "tbl[cols].round(4)"),

    ("md", "## 6. Signifikanztest\n\n"
           "Wie in Notebook 13: Permutationstest auf **Spieler-Ebene** (Ø `over_performance` "
           "pro `fide_id`, nicht pro Partie), um den Cluster-Effekt durch stark ungleiche "
           "Partienzahlen pro Person zu vermeiden. 10.000 Permutationen, fester Seed, kein "
           "scipy/statsmodels nötig. Zellen mit < 8 Spielern in einer Kohorte werden als "
           "`underpowered` markiert."),
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
        "def player_level_means(df_scope, value_col):\n"
        "    return df_scope.groupby(['cohort', 'fide_id'])[value_col].mean().reset_index()\n"
        "\n"
        "SCOPES = {'all': df, 'open_mixed': df[df.scope == 'open_mixed']}\n"
        "sig_rows = []\n"
        "for scope_name, dsub in SCOPES.items():\n"
        "    for band in elo_band_order:\n"
        "        if band == 'unknown':\n"
        "            continue\n"
        "        for sex in SEX_ORDER:\n"
        "            cell = dsub[(dsub.elo_band == band) & (dsub.opponent_sex == sex)]\n"
        "            pm = player_level_means(cell, 'over_performance')\n"
        "            a = pm.loc[pm.cohort == 'top40_female', 'over_performance'].values\n"
        "            b = pm.loc[pm.cohort == 'male_2400_2600', 'over_performance'].values\n"
        "            if len(a) == 0 or len(b) == 0:\n"
        "                continue\n"
        "            diff, p = permutation_test(a, b)\n"
        "            sig_rows.append({\n"
        "                'scope': scope_name, 'elo_band': band, 'opponent_sex': sex,\n"
        "                'n_players_female': len(a), 'n_players_male': len(b),\n"
        "                'mean_diff': round(diff, 4), 'p_value': round(p, 4),\n"
        "                'underpowered': len(a) < 8 or len(b) < 8,\n"
        "            })\n"
        "sig_cols = ['scope', 'elo_band', 'opponent_sex', 'n_players_female',\n"
        "            'n_players_male', 'mean_diff', 'p_value', 'underpowered']\n"
        "sig_table = pd.DataFrame(sig_rows, columns=sig_cols)\n"
        "if sig_table.empty:\n"
        "    print('Keine Zelle hat aktuell Spieler in beiden Kohorten.')\n"
        "sig_table"),

    ("md", "### Zur Veranschaulichung: Nullverteilung der am besten besetzten Zelle"),
    ("code",
        "all_scope = sig_table[sig_table.scope == 'all']\n"
        "if all_scope.empty:\n"
        "    print('Übersprungen: keine Zelle mit Daten in beiden Kohorten.')\n"
        "else:\n"
        "    best = all_scope.sort_values(\n"
        "        ['n_players_female', 'n_players_male'], ascending=False\n"
        "    ).iloc[0]\n"
        "    cell = df[(df.elo_band == best.elo_band) & (df.opponent_sex == best.opponent_sex)]\n"
        "    pm = player_level_means(cell, 'over_performance')\n"
        "    a = pm.loc[pm.cohort == 'top40_female', 'over_performance'].values\n"
        "    b = pm.loc[pm.cohort == 'male_2400_2600', 'over_performance'].values\n"
        "    observed, p_value, diffs = permutation_test(a, b, return_diffs=True)\n"
        "\n"
        "    fig, ax = plt.subplots()\n"
        "    ax.hist(diffs, bins=50, color='#888888', alpha=0.8)\n"
        "    ax.axvline(observed, color=COHORT_PALETTE['top40_female'], lw=2,\n"
        "               label=f'beobachtet ({observed:+.3f}), p={p_value:.4f}')\n"
        "    ax.set_title(f'Permutations-Nullverteilung: {best.elo_band}, vs {best.opponent_sex}')\n"
        "    ax.set_xlabel('Differenz der Spieler-Mittelwerte (top40_female − male_2400_2600)')\n"
        "    ax.legend()\n"
        "    plt.tight_layout(); plt.show()"),

    ("md", "## Fazit\n\n"
           "Die Tabellen in Abschnitt 2 zeigen direkt, ob die Top-40-Frauen der letzten zehn "
           "Jahre bei gleichem absoluten Elo-Niveau besser oder schlechter abschneiden als die "
           "Vergleichspopulation der Männer im selben Band — getrennt nach Gegner-Geschlecht "
           "und mit/ohne Frauen-only-Turniere. Der Permutationstest in Abschnitt 6 zeigt, "
           "welche dieser Unterschiede über reine Stichprobenstreuung hinausgehen.\n\n"
           "**Caveats:**\n"
           "- Die Frauen-Kohorte bleibt mit 65 Spielerinnen (52 mit Partien im Fenster "
           "2016–2025) klein — Zellen an den Rändern der Elo-Range sind oft `underpowered`.\n"
           "- `male_2400_2600` ist eine reine Elo-Band-Population, keine Top-40-Auswahl — das "
           "ist beabsichtigt (Top-40-Männer wären ~2650+, ein anderes Niveau), macht die "
           "beiden Kohorten aber nicht symmetrisch definiert.\n"
           "- Der Frauen-Turnier-Bias bleibt in der `all`-Sicht enthalten; `open_mixed` ist die "
           "fairere Vergleichsbasis, hat aber pro Zelle weniger Partien."),
]


if __name__ == "__main__":
    make_notebook(NBDIR / "14_top40_female_vs_band_men.ipynb", nb14)
