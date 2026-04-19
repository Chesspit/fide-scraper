"""Generate notebook 05_rating_change_sums.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_05.py
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


nb05 = [
    ("md", "# 05 — Rating-Change Summen\n\n"
           "Zentrale Kennzahl: **Summe von `rating_change_weighted`** pro Spieler.\n\n"
           "1. Pro Kalenderjahr\n"
           "2. Gesamt (2015 – aktuellstes Datum)\n"
           "3. Gesamt-Aufteilung nach\n"
           "   - **A** Gegner-Geschlecht (Mann / Frau / unbekannt)\n"
           "   - **B** Eigene Farbe (Weiß / Schwarz)\n"
           "   - **C** Relativer Stärke-Bucket: gleich (±50), stärker (>+50), schwächer (<−50)\n\n"
           "Filter: nur Spieler mit `active = TRUE AND analysis_group IS NOT NULL`."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Datenbasis laden\n\n"
           "Ein gemeinsamer DataFrame, in dem jede Partie mit eigenem Rating, "
           "Gegner-Geschlecht und Stärke-Bucket angereichert ist. "
           "Eine Partie ohne `opponent_fide_id` → `opponent_sex = 'unknown'`."),
    ("code",
        "sql = '''\n"
        "SELECT\n"
        "    gr.fide_id,\n"
        "    gr.period,\n"
        "    EXTRACT(YEAR FROM gr.period)::int AS year,\n"
        "    gr.rating_change_weighted,\n"
        "    gr.color,\n"
        "    gr.opponent_rating,\n"
        "    rh.std_rating AS own_rating,\n"
        "    p.analysis_group,\n"
        "    COALESCE(opp.sex, 'unknown') AS opponent_sex\n"
        "FROM game_results gr\n"
        "JOIN players p ON p.fide_id = gr.fide_id\n"
        "LEFT JOIN rating_history rh ON rh.fide_id = gr.fide_id AND rh.period = gr.period\n"
        "LEFT JOIN players opp ON opp.fide_id = gr.opponent_fide_id\n"
        "WHERE p.active = TRUE AND p.analysis_group IS NOT NULL\n"
        "'''\n"
        "df = load_query(sql)\n"
        "df['rating_change_weighted'] = df['rating_change_weighted'].astype(float)\n"
        "df['own_rating'] = pd.to_numeric(df['own_rating'], errors='coerce')\n"
        "df['opponent_rating'] = pd.to_numeric(df['opponent_rating'], errors='coerce')\n"
        "df['diff'] = df['opponent_rating'] - df['own_rating']\n"
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
        "print('Partien:', len(df))\n"
        "df.head()"),

    ("md", "## 1. Jahres-Summen pro Spieler\n\n"
           "Für die Detailtabelle pro Spieler siehe Notebook 06. "
           "Hier: Ø-Jahressumme pro Gruppe + Boxplot."),
    ("code",
        "yearly = (\n"
        "    df.groupby(['analysis_group', 'fide_id', 'year'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='yearly_sum')\n"
        ")\n"
        "yearly_group = (\n"
        "    yearly.groupby(['analysis_group', 'year'])['yearly_sum']\n"
        "          .agg(['mean', 'median', 'count'])\n"
        "          .round(1).reset_index()\n"
        ")\n"
        "yearly_group.pivot(index='year', columns='analysis_group', values='mean')"),

    ("code",
        "fig, ax = plt.subplots(figsize=(10, 5))\n"
        "sns.boxplot(\n"
        "    data=yearly, x='year', y='yearly_sum',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE, ax=ax,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Σ rating_change_weighted (Jahr)')\n"
        "ax.set_xlabel('Jahr')\n"
        "ax.set_title('Jährliche Rating-Change-Summen pro Spieler')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 2. Gesamtsumme pro Spieler\n\n"
           "Summe über den gesamten beobachteten Zeitraum (abhängig vom aktuellen Backfill-Stand)."),
    ("code",
        "total = (\n"
        "    df.groupby(['analysis_group', 'fide_id'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='total_sum')\n"
        ")\n"
        "total.groupby('analysis_group').agg(\n"
        "    n_players=('fide_id', 'nunique'),\n"
        "    mean_total=('total_sum', 'mean'),\n"
        "    median_total=('total_sum', 'median'),\n"
        "    min_total=('total_sum', 'min'),\n"
        "    max_total=('total_sum', 'max'),\n"
        ").round(1)"),

    ("code",
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=total, x='analysis_group', y='total_sum',\n"
        "    hue='analysis_group', order=GROUP_ORDER, palette=GROUP_PALETTE,\n"
        "    legend=False, ax=ax,\n"
        ")\n"
        "sns.stripplot(\n"
        "    data=total, x='analysis_group', y='total_sum',\n"
        "    order=GROUP_ORDER, color='black', alpha=0.4, size=3, ax=ax,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Σ rating_change_weighted (gesamter Zeitraum)')\n"
        "ax.set_xlabel('')\n"
        "ax.set_title('Gesamtsumme pro Spieler')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 3A. Aufteilung nach Gegner-Geschlecht\n\n"
           "Anmerkung: ~12–16 % der Partien haben noch keine `opponent_fide_id` "
           "(Namensvarianten, v.a. indische Spieler) und werden als `unknown` gezählt."),
    ("code",
        "split_sex = (\n"
        "    df.groupby(['analysis_group', 'fide_id', 'opponent_sex'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='sum')\n"
        ")\n"
        "split_sex_group = split_sex.groupby(['analysis_group', 'opponent_sex']).agg(\n"
        "    mean=('sum', 'mean'), median=('sum', 'median'), n_players=('fide_id', 'nunique'),\n"
        ").round(1).reset_index()\n"
        "split_sex_group"),

    ("code",
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=split_sex, x='opponent_sex', y='sum',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE,\n"
        "    order=['F', 'M', 'unknown'], ax=ax,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Σ rating_change_weighted')\n"
        "ax.set_xlabel('Gegner-Geschlecht')\n"
        "ax.set_title('Gesamt-Summe nach Gegner-Geschlecht')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 3B. Aufteilung nach eigener Farbe"),
    ("code",
        "split_color = (\n"
        "    df.dropna(subset=['color'])\n"
        "      .groupby(['analysis_group', 'fide_id', 'color'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='sum')\n"
        ")\n"
        "split_color.groupby(['analysis_group', 'color']).agg(\n"
        "    mean=('sum', 'mean'), median=('sum', 'median'), n_players=('fide_id', 'nunique'),\n"
        ").round(1)"),

    ("code",
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=split_color, x='color', y='sum',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE,\n"
        "    order=['W', 'B'], ax=ax,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Σ rating_change_weighted')\n"
        "ax.set_xlabel('Eigene Farbe')\n"
        "ax.set_title('Gesamt-Summe nach Farbe')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## 3C. Aufteilung nach Gegnerstärke (relativ)\n\n"
           "- **gleich:** |Gegner − ich| ≤ 50\n"
           "- **stärker:** Gegner > ich + 50\n"
           "- **schwächer:** Gegner < ich − 50\n\n"
           "Partien ohne eigenes Rating in `rating_history` fallen in `unknown`."),
    ("code",
        "split_strength = (\n"
        "    df.groupby(['analysis_group', 'fide_id', 'strength'])['rating_change_weighted']\n"
        "      .sum().reset_index(name='sum')\n"
        ")\n"
        "split_strength.groupby(['analysis_group', 'strength']).agg(\n"
        "    mean=('sum', 'mean'), median=('sum', 'median'), n_players=('fide_id', 'nunique'),\n"
        ").round(1)"),

    ("code",
        "strength_order = ['stärker', 'gleich', 'schwächer', 'unknown']\n"
        "# drop empty categories\n"
        "strength_order = [s for s in strength_order if s in split_strength['strength'].unique()]\n"
        "fig, ax = plt.subplots()\n"
        "sns.boxplot(\n"
        "    data=split_strength, x='strength', y='sum',\n"
        "    hue='analysis_group', hue_order=GROUP_ORDER, palette=GROUP_PALETTE,\n"
        "    order=strength_order, ax=ax,\n"
        ")\n"
        "ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "ax.set_ylabel('Σ rating_change_weighted')\n"
        "ax.set_xlabel('Gegnerstärke relativ')\n"
        "ax.set_title('Gesamt-Summe nach Stärke-Bucket')\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Partienanzahl pro Bucket (Plausibilitätscheck)\n\n"
           "Damit die Summen im Kontext stehen: wie viele Partien gehen in jede Kategorie ein?"),
    ("code",
        "counts = (\n"
        "    df.groupby(['analysis_group', 'opponent_sex', 'color', 'strength']).size()\n"
        "      .reset_index(name='n_games')\n"
        ")\n"
        "counts.groupby('analysis_group')['n_games'].sum().to_frame('total_games')"),
]


if __name__ == "__main__":
    make_notebook(NBDIR / "05_rating_change_sums.ipynb", nb05)
