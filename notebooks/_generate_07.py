"""Generate notebook 07_peer_performance.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_07.py
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
    "import pandas as pd",
    "import numpy as np",
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


nb07 = [
    ("md", "# 07 — Peer- / Stärke-Performance der Top-Spielerinnen\n\n"
           "**Drei Fragen pro Alters-Kohorte, jeweils aufgeteilt nach Gegnerstärke und Gegner-Geschlecht:**\n"
           "1. Wie viele Partien insgesamt — stärker / gleich / schwächer?\n"
           "2. Wie ist der **Gesamt-Erfolg** (Σ `rating_change_weighted`) — insgesamt und je Bucket?\n"
           "3. Wie ist der **Ø Erfolg pro Partie** — insgesamt und je Bucket?\n\n"
           "**Definitionen:**\n"
           "- Stärke-Bucket (Schwelle ±80 Elo): `stärker` = Gegner > ich + 80; `gleich` = |Differenz| ≤ 80; `schwächer` = Gegner < ich − 80\n"
           "- Alters-Kohorten: Alter **in 2015** (Anker wie Notebook 06): <20, 20–30, 30–40, 40–50, >50\n\n"
           "**Filter:** `analysis_group='female_top' AND active=TRUE`, Gegner aufgelöst mit `sex ∈ {M,F}`, eigenes Rating vorhanden."),
    ("code", "\n".join(BOILERPLATE)),

    ("md", "## Datenbasis laden"),
    ("code",
        "sql = '''\n"
        "SELECT\n"
        "    gr.fide_id,\n"
        "    p.birth_year,\n"
        "    rh.std_rating       AS own_rating,\n"
        "    gr.opponent_rating,\n"
        "    opp.sex             AS opponent_sex,\n"
        "    gr.rating_change_weighted\n"
        "FROM game_results gr\n"
        "JOIN players p         ON p.fide_id = gr.fide_id\n"
        "JOIN rating_history rh ON rh.fide_id = gr.fide_id AND rh.period = gr.period\n"
        "JOIN players opp       ON opp.fide_id = gr.opponent_fide_id\n"
        "WHERE p.analysis_group = 'female_top'\n"
        "  AND p.active = TRUE\n"
        "  AND opp.sex IN ('M','F')\n"
        "'''\n"
        "df = load_query(sql)\n"
        "df['rating_change_weighted'] = df['rating_change_weighted'].astype(float)\n"
        "df['diff'] = df['opponent_rating'] - df['own_rating']\n"
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
        "\n"
        "def strength_bucket(d):\n"
        "    if d >  80: return 'stärker'\n"
        "    if d < -80: return 'schwächer'\n"
        "    return 'gleich'\n"
        "df['strength'] = df['diff'].apply(strength_bucket)\n"
        "\n"
        "COHORT_ORDER   = ['<20','20-30','30-40','40-50','>50']\n"
        "STRENGTH_ORDER = ['stärker','gleich','schwächer']\n"
        "SEX_ORDER      = ['F','M']\n"
        "SEX_PALETTE    = {'F':'#c0587e','M':'#4a7ab5'}\n"
        "STRENGTH_PAL   = {'stärker':'#2c7bb6','gleich':'#888888','schwächer':'#d7191c'}\n"
        "\n"
        "# aktive Kohorten (nicht leer)\n"
        "active_cohorts = [c for c in COHORT_ORDER if c in df['cohort'].unique()]\n"
        "print(f'Partien: {len(df):,}   Spielerinnen: {df.fide_id.nunique()}   Kohorten: {active_cohorts}')\n"
        "df.head()"),

    ("md", "## Helper: Tabellen-Bau\n\n"
           "Pro Metrik eine Tabelle mit Zeilen = `(cohort, strength)` + Kohorten-Gesamt-Zeile und\n"
           "Spalten = `vs F | vs M | gesamt`."),
    ("code",
        "def build_table(df, aggfunc, round_to=0):\n"
        "    '''aggfunc: 'size' | 'sum' | 'mean' auf rating_change_weighted.'''\n"
        "    col = 'rating_change_weighted'\n"
        "    def agg(sub):\n"
        "        if aggfunc == 'size': return len(sub)\n"
        "        if aggfunc == 'sum':  return sub[col].sum()\n"
        "        if aggfunc == 'mean': return sub[col].mean() if len(sub) else np.nan\n"
        "\n"
        "    rows = []\n"
        "    for c in active_cohorts:\n"
        "        sub_c = df[df.cohort == c]\n"
        "        # per strength\n"
        "        for s in STRENGTH_ORDER:\n"
        "            sub_cs = sub_c[sub_c.strength == s]\n"
        "            rows.append({\n"
        "                'cohort': c, 'bucket': s,\n"
        "                'vs F':   agg(sub_cs[sub_cs.opponent_sex == 'F']),\n"
        "                'vs M':   agg(sub_cs[sub_cs.opponent_sex == 'M']),\n"
        "                'gesamt': agg(sub_cs),\n"
        "            })\n"
        "        # cohort total\n"
        "        rows.append({\n"
        "            'cohort': c, 'bucket': 'alle',\n"
        "            'vs F':   agg(sub_c[sub_c.opponent_sex == 'F']),\n"
        "            'vs M':   agg(sub_c[sub_c.opponent_sex == 'M']),\n"
        "            'gesamt': agg(sub_c),\n"
        "        })\n"
        "    tbl = pd.DataFrame(rows).set_index(['cohort','bucket'])\n"
        "    if aggfunc == 'size':\n"
        "        tbl = tbl.astype(int)\n"
        "    else:\n"
        "        tbl = tbl.round(round_to if round_to else 1)\n"
        "    return tbl"),

    ("md", "## Frage 1 — Partien insgesamt\n\n"
           "Anzahl Partien pro Kohorte × Stärke-Bucket, aufgeteilt nach Gegner-Geschlecht.\n"
           "`alle` = Summe über alle drei Stärke-Buckets."),
    ("code",
        "t_n = build_table(df, aggfunc='size')\n"
        "t_n"),

    ("code",
        "# Plot: Partien pro Kohorte × Stärke, facettiert vs F / vs M\n"
        "plot_df = t_n.reset_index()\n"
        "plot_df = plot_df[plot_df.bucket != 'alle']\n"
        "plot_df['bucket'] = pd.Categorical(plot_df['bucket'], STRENGTH_ORDER, ordered=True)\n"
        "plot_df['cohort'] = pd.Categorical(plot_df['cohort'], active_cohorts, ordered=True)\n"
        "\n"
        "fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)\n"
        "for ax, sex in zip(axes, SEX_ORDER):\n"
        "    sub = plot_df.pivot(index='cohort', columns='bucket', values=f'vs {sex}')\n"
        "    sub = sub.reindex(active_cohorts)[STRENGTH_ORDER]\n"
        "    sub.plot.bar(ax=ax, color=[STRENGTH_PAL[s] for s in STRENGTH_ORDER],\n"
        "                 edgecolor='white')\n"
        "    ax.set_title(f'Partien vs {sex}')\n"
        "    ax.set_xlabel('Kohorte (Alter 2015)')\n"
        "    ax.set_ylabel('Partien')\n"
        "    ax.legend(title='Stärke')\n"
        "    ax.tick_params(axis='x', rotation=0)\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Frage 2 — Gesamt-Erfolg (Σ rating_change_weighted)\n\n"
           "Summierte Elo-Punkte pro Kohorte × Stärke-Bucket × Gegner-Geschlecht."),
    ("code",
        "t_sum = build_table(df, aggfunc='sum', round_to=1)\n"
        "t_sum"),

    ("code",
        "plot_df = t_sum.reset_index()\n"
        "plot_df = plot_df[plot_df.bucket != 'alle']\n"
        "plot_df['bucket'] = pd.Categorical(plot_df['bucket'], STRENGTH_ORDER, ordered=True)\n"
        "plot_df['cohort'] = pd.Categorical(plot_df['cohort'], active_cohorts, ordered=True)\n"
        "\n"
        "fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)\n"
        "for ax, sex in zip(axes, SEX_ORDER):\n"
        "    sub = plot_df.pivot(index='cohort', columns='bucket', values=f'vs {sex}')\n"
        "    sub = sub.reindex(active_cohorts)[STRENGTH_ORDER]\n"
        "    sub.plot.bar(ax=ax, color=[STRENGTH_PAL[s] for s in STRENGTH_ORDER],\n"
        "                 edgecolor='white')\n"
        "    ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "    ax.set_title(f'Σ Δ vs {sex}')\n"
        "    ax.set_xlabel('Kohorte (Alter 2015)')\n"
        "    ax.set_ylabel('Σ rating_change_weighted')\n"
        "    ax.legend(title='Stärke')\n"
        "    ax.tick_params(axis='x', rotation=0)\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "## Frage 3 — Ø Erfolg pro Partie (Ø rating_change_weighted)\n\n"
           "Durchschnittliche Elo-Änderung je Partie — zeigt, wie effizient gespielt wird."),
    ("code",
        "t_mean = build_table(df, aggfunc='mean', round_to=3)\n"
        "t_mean"),

    ("code",
        "plot_df = t_mean.reset_index()\n"
        "plot_df = plot_df[plot_df.bucket != 'alle']\n"
        "plot_df['bucket'] = pd.Categorical(plot_df['bucket'], STRENGTH_ORDER, ordered=True)\n"
        "plot_df['cohort'] = pd.Categorical(plot_df['cohort'], active_cohorts, ordered=True)\n"
        "\n"
        "fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)\n"
        "for ax, sex in zip(axes, SEX_ORDER):\n"
        "    sub = plot_df.pivot(index='cohort', columns='bucket', values=f'vs {sex}')\n"
        "    sub = sub.reindex(active_cohorts)[STRENGTH_ORDER]\n"
        "    sub.plot.bar(ax=ax, color=[STRENGTH_PAL[s] for s in STRENGTH_ORDER],\n"
        "                 edgecolor='white')\n"
        "    ax.axhline(0, color='grey', lw=0.8, ls='--')\n"
        "    ax.set_title(f'Ø Δ pro Partie vs {sex}')\n"
        "    ax.set_xlabel('Kohorte (Alter 2015)')\n"
        "    ax.set_ylabel('Ø rating_change_weighted')\n"
        "    ax.legend(title='Stärke')\n"
        "    ax.tick_params(axis='x', rotation=0)\n"
        "    for p in ax.patches:\n"
        "        h = p.get_height()\n"
        "        if pd.notna(h) and abs(h) > 0.01:\n"
        "            ax.annotate(f'{h:+.2f}', (p.get_x()+p.get_width()/2, h),\n"
        "                        ha='center', va='bottom' if h>=0 else 'top', fontsize=7)\n"
        "plt.tight_layout(); plt.show()"),

    ("md", "### CSV-Export der drei Tabellen"),
    ("code",
        "with pd.ExcelWriter('peer_performance.xlsx') as xw:\n"
        "    t_n.to_excel(xw,    sheet_name='Partien')\n"
        "    t_sum.to_excel(xw,  sheet_name='Erfolg_Summe')\n"
        "    t_mean.to_excel(xw, sheet_name='Erfolg_pro_Partie')\n"
        "t_n.to_csv('peer_performance_n.csv')\n"
        "t_sum.to_csv('peer_performance_sum.csv')\n"
        "t_mean.to_csv('peer_performance_mean.csv')\n"
        "print('wrote peer_performance.xlsx + 3 CSVs')"),
]


if __name__ == "__main__":
    make_notebook(NBDIR / "07_peer_performance.ipynb", nb07)
