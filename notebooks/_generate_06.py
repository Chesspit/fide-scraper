"""Generate notebook 06_age_cohorts.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_06.py
"""

from pathlib import Path
import nbformat as nbf

NBDIR = Path(__file__).resolve().parent

BOILERPLATE = [
    "import sys",
    "from pathlib import Path",
    "sys.path.insert(0, str(Path.cwd()))",
    "from _setup import load_query, apply_style, GROUP_PALETTE, GROUP_ORDER",
    "",
    "import pandas as pd",
    "import matplotlib.pyplot as plt",
    "import seaborn as sns",
    "",
    "apply_style()",
    "pd.set_option('display.max_rows', 250)",
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


nb06 = [
    ("md", "# 06 — Alters-Kohorten & Spieler-Tabelle\n\n"
           "**Frage:** Wo kommen die Rating-Zuwächse her? Treiben jüngere oder ältere Spieler*innen die Verbesserung?\n\n"
           "1. Kohortenbildung nach Alter **in 2015** (erste Periode): <20, 20–30, 30–40, 40–50, >50\n"
           "2. Pro Kohorte × Gruppe: Mean/Median der kumulativen Rating-Änderung\n"
           "3. Heatmap Kohorte × Jahr\n"
           "4. **Spieler-Tabelle** mit Name, aktueller ELO, Gesamt-Δ, Δ pro Jahr (2015–2025)\n\n"
           "Filter: `active = TRUE AND analysis_group IS NOT NULL`."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Datenbasis laden"),
    ("code",
        "sql = '''\n"
        "SELECT\n"
        "    gr.fide_id,\n"
        "    p.name,\n"
        "    p.analysis_group,\n"
        "    p.std_rating AS current_rating,\n"
        "    p.birth_year,\n"
        "    EXTRACT(YEAR FROM gr.period)::int AS year,\n"
        "    gr.rating_change_weighted\n"
        "FROM game_results gr\n"
        "JOIN players p ON p.fide_id = gr.fide_id\n"
        "WHERE p.active = TRUE AND p.analysis_group IS NOT NULL\n"
        "'''\n"
        "df = load_query(sql)\n"
        "df['rating_change_weighted'] = df['rating_change_weighted'].astype(float)\n"
        "df['age_2015'] = 2015 - df['birth_year']\n"
        "\n"
        "def age_bucket(a):\n"
        "    if pd.isna(a): return 'unknown'\n"
        "    if a < 20: return '<20'\n"
        "    if a < 30: return '20-30'\n"
        "    if a < 40: return '30-40'\n"
        "    if a < 50: return '40-50'\n"
        "    return '>50'\n"
        "df['cohort'] = df['age_2015'].apply(age_bucket)\n"
        "COHORT_ORDER = ['<20', '20-30', '30-40', '40-50', '>50']\n"
        "print('Partien:', len(df), '| Spieler:', df['fide_id'].nunique())\n"
        "df[['fide_id','name','analysis_group','birth_year','age_2015','cohort']].drop_duplicates('fide_id').head()"),

    ("md", "## 1. Kohorten-Besetzung\n\nWie viele Spieler pro Kohorte × Gruppe?"),
    ("code",
        "players_meta = df[['fide_id','analysis_group','cohort']].drop_duplicates('fide_id')\n"
        "cohort_counts = (\n"
        "    players_meta.groupby(['analysis_group','cohort']).size()\n"
        "    .unstack('cohort').reindex(columns=COHORT_ORDER, fill_value=0)\n"
        ")\n"
        "cohort_counts"),

    ("md", "## 2. Kumulativer Rating-Gewinn pro Kohorte\n\n"
           "Pro Spieler Σ `rating_change_weighted` über 2015–2025, dann Mean/Median je Kohorte × Gruppe."),
    ("code",
        "total_per_player = (\n"
        "    df.groupby(['analysis_group','cohort','fide_id'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='total_sum')\n"
        ")\n"
        "cohort_stats = total_per_player.groupby(['analysis_group','cohort']).agg(\n"
        "    n_players=('fide_id','nunique'),\n"
        "    mean_total=('total_sum','mean'),\n"
        "    median_total=('total_sum','median'),\n"
        ").round(1).reset_index()\n"
        "cohort_stats.pivot(index='cohort', columns='analysis_group', values='mean_total').reindex(COHORT_ORDER)"),

    ("code",
        "fig, ax = plt.subplots(figsize=(10, 5))\n"
        "sns.boxplot(\n"
        "    data=total_per_player, x='cohort', y='total_sum',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE,\n"
        "    order=COHORT_ORDER, ax=ax,\n"
        ")\n"
        "sns.stripplot(\n"
        "    data=total_per_player, x='cohort', y='total_sum',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, dodge=True,\n"
        "    order=COHORT_ORDER, color='black', alpha=0.3, size=3, ax=ax,\n"
        "    legend=False,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Σ rating_change_weighted (2015–2025)')\n"
        "ax.set_xlabel('Alter in 2015')\n"
        "ax.set_title('Kumulativer Rating-Gewinn pro Alterskohorte')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 3. Heatmap Kohorte × Jahr\n\n"
           "Mean Jahres-Summe pro Kohorte × Jahr, getrennt nach Gruppe. "
           "Zeigt, ob Gewinne gleichmäßig verteilt sind oder einzelne Jahre/Kohorten dominieren."),
    ("code",
        "yearly_per_player = (\n"
        "    df.groupby(['analysis_group','cohort','fide_id','year'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='yearly_sum')\n"
        ")\n"
        "yearly_cohort = (\n"
        "    yearly_per_player.groupby(['analysis_group','cohort','year'])['yearly_sum']\n"
        "                     .mean().round(1).reset_index()\n"
        ")\n"
        "\n"
        "fig, axes = plt.subplots(1, 2, figsize=(13, 4), sharey=True)\n"
        "for ax, grp in zip(axes, GROUP_ORDER):\n"
        "    sub = (yearly_cohort[yearly_cohort['analysis_group']==grp]\n"
        "           .pivot(index='cohort', columns='year', values='yearly_sum')\n"
        "           .reindex(COHORT_ORDER))\n"
        "    sns.heatmap(sub, annot=True, fmt='.1f', cmap='RdYlGn', center=0, ax=ax,\n"
        "                cbar_kws={'label':'Ø Δ pro Spieler'})\n"
        "    ax.set_title(grp)\n"
        "    ax.set_xlabel('Jahr'); ax.set_ylabel('Kohorte')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 4. Spieler-Tabelle\n\n"
           "Pro Spieler: aktueller ELO, Gesamt-Δ, Jahres-Δ 2015–2025. Sortiert alphabetisch."),
    ("code",
        "yearly_wide = (\n"
        "    yearly_per_player.pivot_table(\n"
        "        index='fide_id', columns='year', values='yearly_sum', fill_value=0\n"
        "    ).round(1)\n"
        ")\n"
        "yearly_wide.columns = [f'Δ {int(c)}' for c in yearly_wide.columns]\n"
        "\n"
        "meta = df[['fide_id','name','analysis_group','current_rating','age_2015','cohort']].drop_duplicates('fide_id').set_index('fide_id')\n"
        "totals = total_per_player.set_index('fide_id')['total_sum'].round(1).rename('Σ 2015–2025')\n"
        "\n"
        "table = meta.join(totals).join(yearly_wide)\n"
        "table = table.rename(columns={\n"
        "    'name':'Name','analysis_group':'Gruppe',\n"
        "    'current_rating':'ELO aktuell','age_2015':'Alter 2015','cohort':'Kohorte',\n"
        "})\n"
        "table = table.sort_values('Name').reset_index(drop=True)\n"
        "print(f'{len(table)} Spieler')\n"
        "table"),

    ("md", "### CSV-Export"),
    ("code",
        "out = Path('player_rating_changes.csv')\n"
        "table.to_csv(out, index=False)\n"
        "print(f'wrote {out}')"),
]


if __name__ == "__main__":
    make_notebook(NBDIR / "06_age_cohorts.ipynb", nb06)
