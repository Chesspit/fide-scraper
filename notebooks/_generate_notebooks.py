"""One-shot generator for the 4 analysis notebooks.

Run from project root:
    .venv/bin/python notebooks/_generate_notebooks.py
"""

from pathlib import Path
import nbformat as nbf

NBDIR = Path(__file__).resolve().parent

BOILERPLATE = [
    "import sys",
    "from pathlib import Path",
    "sys.path.insert(0, str(Path.cwd()))",
    "from _setup import load_view, load_query, apply_style, GROUP_PALETTE, GROUP_ORDER",
    "",
    "import pandas as pd",
    "import matplotlib.pyplot as plt",
    "import seaborn as sns",
    "",
    "apply_style()",
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


# ------------------------------------------------------------
# 01 — Opponent structure
# ------------------------------------------------------------
nb01 = [
    ("md", "# 01 — Gegnerstruktur\n\n"
           "Frage: *Unterscheidet sich die Gegnerstruktur zwischen female_top (ELO 2400–2600) "
           "und male_control (age-matched)?*\n\n"
           "Kennzahl: `avg_opponent_diff` = Ø Rating der Gegner − eigenes Rating pro Periode."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Daten laden"),
    ("code",
        "df = load_view('v_opponent_strength')\n"
        "df['period'] = pd.to_datetime(df['period'])\n"
        "df[['avg_opponent_rating', 'avg_opponent_diff', 'own_rating']] = (\n"
        "    df[['avg_opponent_rating', 'avg_opponent_diff', 'own_rating']].astype(float)\n"
        ")\n"
        "print(df.shape)\n"
        "df.head()"),

    ("md", "## Summary pro Gruppe"),
    ("code",
        "summary = df.groupby('analysis_group').agg(\n"
        "    n_player_periods=('fide_id', 'count'),\n"
        "    n_players=('fide_id', 'nunique'),\n"
        "    mean_opp_rating=('avg_opponent_rating', 'mean'),\n"
        "    mean_opp_diff=('avg_opponent_diff', 'mean'),\n"
        "    median_opp_diff=('avg_opponent_diff', 'median'),\n"
        ").round(1)\n"
        "summary"),

    ("md", "## Boxplot — Ø Rating-Differenz pro Gruppe\n\n"
           "Negative Werte = Gegner im Schnitt schwächer als man selbst."),
    ("code",
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=df, x='analysis_group', y='avg_opponent_diff',\n"
        "    order=GROUP_ORDER, palette=GROUP_PALETTE, ax=ax,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Ø Gegnerrating − eigenes Rating')\n"
        "ax.set_xlabel('')\n"
        "ax.set_title('Gegnerstruktur pro (Spieler, Periode)')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Verteilung der einzelnen Gegner-Ratings\n\n"
           "Für einen direkten Histogramm-Vergleich gehen wir auf Einzelpartie-Ebene."),
    ("code",
        "raw = load_query('''\n"
        "    SELECT p.analysis_group, gr.opponent_rating\n"
        "    FROM game_results gr\n"
        "    JOIN players p USING (fide_id)\n"
        "    WHERE p.active = TRUE AND p.analysis_group IS NOT NULL\n"
        "      AND gr.opponent_rating IS NOT NULL\n"
        "''')\n"
        "fig, ax = plt.subplots()\n"
        "sns.histplot(\n"
        "    data=raw, x='opponent_rating', hue='analysis_group',\n"
        "    bins=40, stat='density', common_norm=False,\n"
        "    palette=GROUP_PALETTE, hue_order=GROUP_ORDER, ax=ax,\n"
        ")\n"
        "ax.set_xlabel('Gegner-Rating')\n"
        "ax.set_title('Verteilung der Gegner-Ratings')\n"
        "plt.tight_layout(); plt.show()"),
]
make_notebook(NBDIR / "01_opponent_structure.ipynb", nb01)


# ------------------------------------------------------------
# 02 — Rating volatility
# ------------------------------------------------------------
nb02 = [
    ("md", "# 02 — Rating-Volatilität\n\n"
           "Frage: *Schwanken die Ratings in einer Gruppe stärker als in der anderen?*\n\n"
           "Kennzahl: `normalized_volatility` = Ø |rating_change| geteilt durch K-Faktor — "
           "das macht Spieler mit unterschiedlichem K vergleichbar."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Daten laden"),
    ("code",
        "df = load_view('v_rating_volatility')\n"
        "df['period'] = pd.to_datetime(df['period'])\n"
        "df[['avg_abs_change', 'normalized_volatility']] = (\n"
        "    df[['avg_abs_change', 'normalized_volatility']].astype(float)\n"
        ")\n"
        "df = df.dropna(subset=['normalized_volatility'])\n"
        "print(df.shape)\n"
        "df.head()"),

    ("md", "## Summary pro Gruppe"),
    ("code",
        "df.groupby('analysis_group').agg(\n"
        "    n=('fide_id', 'count'),\n"
        "    mean_abs_change=('avg_abs_change', 'mean'),\n"
        "    mean_norm_vol=('normalized_volatility', 'mean'),\n"
        "    median_norm_vol=('normalized_volatility', 'median'),\n"
        ").round(3)"),

    ("md", "## Boxplot — normalisierte Volatilität"),
    ("code",
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=df, x='analysis_group', y='normalized_volatility',\n"
        "    order=GROUP_ORDER, palette=GROUP_PALETTE, ax=ax,\n"
        ")\n"
        "ax.set_ylabel('Ø |rating_change| / K-Faktor')\n"
        "ax.set_xlabel('')\n"
        "ax.set_title('Normalisierte Rating-Volatilität pro (Spieler, Periode)')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Volatilität über Zeit\n\n"
           "Ø normalisierte Volatilität pro Periode und Gruppe."),
    ("code",
        "ts = df.groupby(['analysis_group', 'period'])['normalized_volatility'].mean().reset_index()\n"
        "fig, ax = plt.subplots()\n"
        "sns.lineplot(\n"
        "    data=ts, x='period', y='normalized_volatility',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE, ax=ax,\n"
        ")\n"
        "ax.set_ylabel('Ø normalisierte Volatilität')\n"
        "ax.set_title('Rating-Volatilität über Zeit')\n"
        "plt.tight_layout(); plt.show()"),
]
make_notebook(NBDIR / "02_rating_volatility.ipynb", nb02)


# ------------------------------------------------------------
# 03 — Tournament frequency
# ------------------------------------------------------------
nb03 = [
    ("md", "# 03 — Turnierfrequenz\n\n"
           "Frage: *Spielen die beiden Gruppen gleich oft?*\n\n"
           "Kennzahlen: `num_games` und `num_tournaments` pro (Spieler, Periode)."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Daten laden"),
    ("code",
        "df = load_view('v_tournament_frequency')\n"
        "df['period'] = pd.to_datetime(df['period'])\n"
        "print(df.shape)\n"
        "df.head()"),

    ("md", "## Summary pro Gruppe"),
    ("code",
        "df.groupby('analysis_group').agg(\n"
        "    n_active_periods=('fide_id', 'count'),\n"
        "    n_players=('fide_id', 'nunique'),\n"
        "    mean_games_per_period=('num_games', 'mean'),\n"
        "    median_games_per_period=('num_games', 'median'),\n"
        "    mean_tournaments=('num_tournaments', 'mean'),\n"
        ").round(2)"),

    ("md", "## Boxplot — Partien pro Periode"),
    ("code",
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=df, x='analysis_group', y='num_games',\n"
        "    order=GROUP_ORDER, palette=GROUP_PALETTE, ax=ax,\n"
        ")\n"
        "ax.set_ylabel('Partien pro Periode')\n"
        "ax.set_xlabel('')\n"
        "ax.set_title('Turnierfrequenz pro (Spieler, Periode)')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Saisonalität — Ø Partien pro Kalendermonat"),
    ("code",
        "df['month'] = df['period'].dt.month\n"
        "monthly = df.groupby(['analysis_group', 'month'])['num_games'].mean().reset_index()\n"
        "fig, ax = plt.subplots()\n"
        "sns.lineplot(\n"
        "    data=monthly, x='month', y='num_games',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE,\n"
        "    marker='o', ax=ax,\n"
        ")\n"
        "ax.set_xticks(range(1, 13))\n"
        "ax.set_xlabel('Kalendermonat')\n"
        "ax.set_ylabel('Ø Partien / Periode')\n"
        "ax.set_title('Saisonalität')\n"
        "plt.tight_layout(); plt.show()"),
]
make_notebook(NBDIR / "03_tournament_frequency.ipynb", nb03)


# ------------------------------------------------------------
# 04 — Rating progression
# ------------------------------------------------------------
nb04 = [
    ("md", "# 04 — Rating-Progression\n\n"
           "Frage: *Entwickelt sich das Rating der beiden Gruppen über Zeit unterschiedlich?*\n\n"
           "Kennzahl: `rating_delta_from_start` pro Spieler × Periode (Median + IQR pro Gruppe)."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Daten laden"),
    ("code",
        "df = load_view('v_rating_progression')\n"
        "df['period'] = pd.to_datetime(df['period'])\n"
        "df['std_rating'] = df['std_rating'].astype(float)\n"
        "df['rating_delta_from_start'] = df['rating_delta_from_start'].astype(float)\n"
        "print(df.shape)\n"
        "df.head()"),

    ("md", "## Summary Start vs. aktuellster Stand"),
    ("code",
        "first = df.sort_values('period').groupby('fide_id').first()\n"
        "last = df.sort_values('period').groupby('fide_id').last()\n"
        "delta = last['std_rating'] - first['std_rating']\n"
        "combined = first[['analysis_group']].assign(\n"
        "    start_rating=first['std_rating'],\n"
        "    end_rating=last['std_rating'],\n"
        "    delta=delta,\n"
        ")\n"
        "combined.groupby('analysis_group').agg(\n"
        "    n=('delta', 'count'),\n"
        "    mean_start=('start_rating', 'mean'),\n"
        "    mean_end=('end_rating', 'mean'),\n"
        "    mean_delta=('delta', 'mean'),\n"
        "    median_delta=('delta', 'median'),\n"
        ").round(1)"),

    ("md", "## Rating-Delta über Zeit — Median + IQR pro Gruppe"),
    ("code",
        "agg = (\n"
        "    df.groupby(['analysis_group', 'period'])['rating_delta_from_start']\n"
        "      .agg(['median',\n"
        "            lambda s: s.quantile(0.25),\n"
        "            lambda s: s.quantile(0.75)])\n"
        "      .rename(columns={'<lambda_0>': 'q25', '<lambda_1>': 'q75'})\n"
        "      .reset_index()\n"
        ")\n"
        "fig, ax = plt.subplots()\n"
        "for grp in GROUP_ORDER:\n"
        "    sub = agg[agg['analysis_group'] == grp]\n"
        "    color = GROUP_PALETTE[grp]\n"
        "    ax.plot(sub['period'], sub['median'], color=color, lw=2, label=grp)\n"
        "    ax.fill_between(sub['period'], sub['q25'], sub['q75'], color=color, alpha=0.2)\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Δ Rating vs. erste beobachtete Periode')\n"
        "ax.set_title('Rating-Progression — Median + IQR')\n"
        "ax.legend()\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Individueller Rating-Verlauf"),
    ("code",
        "fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)\n"
        "for ax, grp in zip(axes, GROUP_ORDER):\n"
        "    sub = df[df['analysis_group'] == grp]\n"
        "    for fid, g in sub.groupby('fide_id'):\n"
        "        ax.plot(g['period'], g['std_rating'], color=GROUP_PALETTE[grp], alpha=0.25, lw=0.8)\n"
        "    ax.set_title(grp)\n"
        "    ax.set_xlabel('Periode')\n"
        "axes[0].set_ylabel('Standard-Rating')\n"
        "plt.suptitle('Einzelverläufe pro Spieler')\n"
        "plt.tight_layout(); plt.show()"),
]
make_notebook(NBDIR / "04_rating_progression.ipynb", nb04)
