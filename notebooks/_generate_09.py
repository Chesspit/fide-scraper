"""Generate notebook 09_rating_history.ipynb.

Run from project root:
    .venv/bin/python notebooks/_generate_09.py
"""

from pathlib import Path
import nbformat as nbf

NBDIR = Path(__file__).resolve().parent


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


SQL_THRESHOLDS = """
SELECT
    rh.period,
    p.sex,
    COUNT(*)                                              AS total_rated,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2200)  AS ge_2200,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2300)  AS ge_2300,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2400)  AS ge_2400,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2500)  AS ge_2500,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2600)  AS ge_2600,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2700)  AS ge_2700,
    COUNT(*) FILTER (WHERE rh.published_rating >= 2800)  AS ge_2800
FROM rating_history rh
JOIN players p USING(fide_id)
WHERE rh.published_rating IS NOT NULL
  AND p.sex IN ('M', 'F')
GROUP BY rh.period, p.sex
ORDER BY rh.period, p.sex
"""

THRESHOLDS = [2200, 2300, 2400, 2500, 2600, 2700, 2800]
COLORS_M = "#4a7ab5"
COLORS_F = "#c0587e"

nb09 = [

("md", """# 09 — Rating-Geschichte: Schwellen, Inflation & Geschlechtsverteilung

Wie hat sich die Zahl der Spieler über definierten ELO-Schwellen von 2006 bis 2026 entwickelt?
Drei Perspektiven: Gesamt, Männer, Frauen — plus Kontext (Anteil unter 2200).

**Datenquelle:** `rating_history.published_rating` für ~1,8 Mio. Spieler,
195 Perioden Jan 2006 – Apr 2026 (ohne Scraping-Daten).
"""),

("code", """\
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from _setup import load_query, apply_style

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
import numpy as np

apply_style()
plt.rcParams["figure.figsize"] = (12, 6)
"""),

("md", "## Daten laden"),

("code", f"""\
SQL = \"\"\"{SQL_THRESHOLDS}\"\"\"

raw = load_query(SQL)
raw["period"] = pd.to_datetime(raw["period"])
print(f"{{len(raw)}} Zeilen, {{raw.period.nunique()}} Perioden, {{raw.sex.unique()}}")
raw.tail(4)
"""),

("code", """\
# Gesamt (M+F kombiniert)
total = (raw.groupby("period")
           [["total_rated","ge_2200","ge_2300","ge_2400",
             "ge_2500","ge_2600","ge_2700","ge_2800"]]
           .sum()
           .reset_index())

m = raw[raw.sex == "M"].set_index("period")
f = raw[raw.sex == "F"].set_index("period")

THRESHOLDS = [2200, 2300, 2400, 2500, 2600, 2700, 2800]
cols = [f"ge_{t}" for t in THRESHOLDS]
"""),

("md", "## 1. Absolute Entwicklung — Alle Spieler"),

("code", """\
fig, ax = plt.subplots(figsize=(13, 6))
palette = sns.color_palette("viridis_r", len(THRESHOLDS))

for col, t, c in zip(cols, THRESHOLDS, palette):
    ax.plot(total.period, total[col], label=f"≥ {t}", color=c, linewidth=1.8)

ax.set(title="Spieler über ELO-Schwelle (Männer + Frauen kombiniert)",
       xlabel="", ylabel="Anzahl Spieler")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{int(x):,}"))
ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=9)
plt.tight_layout()
plt.show()
"""),

("md", "## 2. Gesamt: Männer vs. Frauen — je Schwelle (Small Multiples)"),

("code", """\
fig, axes = plt.subplots(2, 4, figsize=(16, 8), sharey=False)
axes = axes.flatten()

for i, (col, t) in enumerate(zip(cols, THRESHOLDS)):
    ax = axes[i]
    ax.plot(m.index, m[col], color="#4a7ab5", label="Männer", linewidth=1.6)
    ax.plot(f.index, f[col], color="#c0587e", label="Frauen", linewidth=1.6)
    ax.set_title(f"≥ {t}", fontsize=11, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{int(x):,}"))
    ax.tick_params(axis="x", rotation=30, labelsize=8)
    if i == 0:
        ax.legend(fontsize=9)

axes[-1].set_visible(False)
fig.suptitle("Spieler über ELO-Schwelle — Männer vs. Frauen", fontsize=13, fontweight="bold")
plt.tight_layout()
plt.show()
"""),

("md", "## 3. Frauenanteil je Schwelle über Zeit"),

("code", """\
fig, ax = plt.subplots(figsize=(13, 6))
palette = sns.color_palette("plasma", len(THRESHOLDS))

for col, t, c in zip(cols, THRESHOLDS, palette):
    m_vals = m[col].reindex(total.period).values
    f_vals = f[col].reindex(total.period).values
    share = np.where((m_vals + f_vals) > 0,
                     100 * f_vals / (m_vals + f_vals), np.nan)
    ax.plot(total.period, share, label=f"≥ {t}", color=c, linewidth=1.8)

ax.set(title="Frauenanteil (%) je ELO-Schwelle — 2006 bis 2026",
       xlabel="", ylabel="Frauenanteil (%)")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.1f}%"))
ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=9)
plt.tight_layout()
plt.show()
"""),

("md", "## 4. Relative Inflation — Anteil an allen gerateten Spielern"),

("code", """\
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

for ax, sex, color, label in [
    (axes[0], "M", "#4a7ab5", "Männer"),
    (axes[1], "F", "#c0587e", "Frauen"),
]:
    subset = raw[raw.sex == sex].set_index("period")
    palette = sns.color_palette("viridis_r", len(THRESHOLDS))
    for col, t, c in zip(cols, THRESHOLDS, palette):
        share = 100 * subset[col] / subset["total_rated"]
        ax.plot(subset.index, share, label=f"≥ {t}", color=c, linewidth=1.6)
    ax.set(title=f"{label}: Anteil aller Gerateten über Schwelle",
           ylabel="Anteil (%)", xlabel="")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.2f}%"))
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", rotation=30)

plt.suptitle("Relative Inflation — (Spieler ≥X) / (alle Gerateten)", fontsize=12)
plt.tight_layout()
plt.show()
"""),

("md", "## 5. Aktueller Snapshot — Pyramide nach Rating-Klasse"),

("code", """\
latest = raw[raw.period == raw.period.max()].copy()

buckets = [
    ("<2200",  "total_rated",  "ge_2200"),
    ("2200-2299", "ge_2200", "ge_2300"),
    ("2300-2399", "ge_2300", "ge_2400"),
    ("2400-2499", "ge_2400", "ge_2500"),
    ("2500-2599", "ge_2500", "ge_2600"),
    ("2600-2699", "ge_2600", "ge_2700"),
    ("2700-2799", "ge_2700", "ge_2800"),
    ("≥2800",     "ge_2800",  None),
]

rows = []
for label, upper_col, lower_col in buckets:
    for _, row in latest.iterrows():
        n = int(row[upper_col]) - (int(row[lower_col]) if lower_col else 0)
        rows.append({"Klasse": label, "Geschlecht": "Männer" if row.sex=="M" else "Frauen", "n": n})

pyramid = pd.DataFrame(rows)
pivot = pyramid.pivot(index="Klasse", columns="Geschlecht", values="n")

# Reihenfolge
order = [b[0] for b in buckets]
pivot = pivot.reindex(order)

fig, ax = plt.subplots(figsize=(12, 6))
x = np.arange(len(pivot))
width = 0.35
ax.bar(x - width/2, pivot["Männer"], width, label="Männer", color="#4a7ab5")
ax.bar(x + width/2, pivot["Frauen"], width, label="Frauen", color="#c0587e")
ax.set(title=f"Rating-Pyramide — Snapshot {raw.period.max().strftime('%b %Y')}",
       ylabel="Anzahl Spieler", xticks=x, xticklabels=order)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{int(x):,}"))
ax.legend()
plt.tight_layout()
plt.show()

# Tabelle
print("\\nAbsolute Zahlen:")
display(pivot.assign(Gesamt=pivot.sum(axis=1))
        .assign(Frauenanteil=lambda d: (100*d["Frauen"]/d["Gesamt"]).round(2).astype(str)+"%"))
"""),

("md", "## 6. Kontext: Anteil unter 2200"),

("code", """\
fig, ax = plt.subplots(figsize=(12, 5))

for sex, color, label in [("M","#4a7ab5","Männer"), ("F","#c0587e","Frauen")]:
    s = raw[raw.sex==sex].set_index("period")
    pct_below = 100 * (s["total_rated"] - s["ge_2200"]) / s["total_rated"]
    ax.plot(s.index, pct_below, color=color, label=label, linewidth=1.8)

ax.set(title="Anteil der Spieler unter ELO 2200 (% aller Gerateten)",
       ylabel="Anteil (%)", xlabel="")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.1f}%"))
ax.legend()
plt.tight_layout()
plt.show()
"""),

]

if __name__ == "__main__":
    make_notebook(NBDIR / "09_rating_history.ipynb", nb09)
