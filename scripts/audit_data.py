#!/usr/bin/env python3
"""Datenprüfung ab Stichtag — vollständig und Elo-plausibel? (read-only)

Maßstab ist die offizielle FIDE-Liste: Soll ist jeder Spieler mit mindestens
einer gewerteten Partie laut Liste im Zeitraum, auch heute inaktive. Details
zu den vier Prüfebenen: orchestrator/audit.py.

Usage:
    python scripts/audit_data.py --since 2020-01
    python scripts/audit_data.py --since 2026-01 --federation LIE,AUT --sample 5
    python scripts/audit_data.py --since 2020-01 --layer 0,1 --report audit.md
    python scripts/audit_data.py --since 2012-08 --until 2019-12 --csv findings.csv

Voraussetzung: Migration 018 (fn_fide_expected) für Ebene 3.
Exit-Code 1, wenn ein Check hart anschlägt (cron-tauglich), 2 bei Abbruch.
"""

import argparse
import csv
import json
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.audit import (
    DEFAULT_CHAIN_THRESHOLD_PCT,
    DEFAULT_CHAIN_TOLERANCE,
    DEFAULT_FORMULA_THRESHOLD_PCT,
    DEFAULT_FEWER_THRESHOLD_PCT,
    DEFAULT_JUMP,
    DEFAULT_NO_DATA_THRESHOLD_PCT,
    HARD,
    INFO,
    LAYERS,
    OK,
    SOFT,
    AuditResult,
    parse_period,
    run_audit,
)
from orchestrator.setup_db import connect

logger = logging.getLogger(__name__)

MARK = {OK: "✓", INFO: "ℹ", SOFT: "△", HARD: "✗"}
LAYER_TITLE = {
    0: "Ebene 0 — Referenz (offizielle Listen)",
    1: "Ebene 1 — Vollständigkeit Spieler/Perioden",
    2: "Ebene 2 — Vollständigkeit Partien",
    3: "Ebene 3 — Elo-Plausibilität",
}


def _fmt_pct(v: float) -> str:
    return f"{v:.1f} %".replace(".", ",")


def _n(v) -> str:
    return f"{v:,}".replace(",", ".")


def _chain(h: dict) -> str:
    if not h.get("chain_checked"):
        return "nicht prüfbar"
    return (f"{_fmt_pct(h['pct_chain_ok'])} ok, "
            f"{_fmt_pct(h['pct_chain_unexplained'])} unerklärt")


def summary_line(res: AuditResult) -> str:
    h = res.headline
    return (f"Audit {res.since:%Y-%m}–{res.periods[-1]:%Y-%m}: "
            f"{_fmt_pct(h['pct_periods_complete'])} Perioden vollständig, "
            f"{_fmt_pct(h['pct_games_found'])} Partien, "
            f"Kette {_chain(h)}, "
            f"{h['hard_checks']} harte / {h['soft_checks']} weiche Checks"
            if res.periods else "Audit: keine Perioden im Zeitraum")


def print_scorecard(res: AuditResult, limit: int) -> None:
    h = res.headline
    print()
    print("=" * 72)
    print(f"  Datenprüfung {res.since:%Y-%m} bis "
          f"{res.periods[-1]:%Y-%m}" if res.periods else "  Datenprüfung (leer)")
    if res.federations:
        print(f"  Föderationen: {', '.join(res.federations)}")
    print("=" * 72)
    print(f"  Soll-Kombinationen (Spieler×Periode mit Partien laut Liste): {_n(h['soll_combos'])}")
    if "soll_players" in h:
        print(f"  Soll-Spieler: {_n(h['soll_players'])}, davon mind. einmal gescrapt: "
              f"{_fmt_pct(h['pct_players_touched'])}")
    print(f"  Perioden gescrapt:     {_fmt_pct(h['pct_periods_scraped'])}")
    print(f"  Perioden vollständig:  {_fmt_pct(h['pct_periods_complete'])}  (Partienzahl = Liste)")
    print(f"  Partien vorhanden:     {_fmt_pct(h['pct_games_found'])}  "
          f"({_n(h['found_games'])} von {_n(h['listed_games'])})")
    print(f"  Kette Liste↔Partien:   {_chain(h)}")
    if res.skipped_periods:
        print(f"  ⚠ ohne Referenzliste übersprungen: "
              f"{', '.join(f'{p:%Y-%m}' for p in res.skipped_periods)}")

    for layer in LAYERS:
        checks = [c for c in res.checks if c.layer == layer]
        if not checks:
            continue
        print(f"\n  {LAYER_TITLE[layer]}")
        for c in checks:
            ratio = (f"{_n(c.n_findings)} / {_n(c.n_checked)} ({_fmt_pct(c.pct)})"
                     if c.n_checked else _n(c.n_findings))
            print(f"    {MARK[c.severity]} {c.id:<28} {ratio}")
            print(f"        {c.title}")
            if c.note:
                print(f"        {c.note}")
            if c.severity != OK and c.worst_periods:
                print("        schlechteste Perioden: " + ", ".join(
                    f"{p[:7]} {_fmt_pct(v)}" for p, v in c.worst_periods[:5]))
            if c.severity in (SOFT, HARD):
                if c.by_federation:
                    print("        Top: " + ", ".join(
                        f"{k} {_n(v)}" for k, v in c.by_federation.most_common(6)))
                for s in c.sample[:limit]:
                    print("        · " + json.dumps(s, ensure_ascii=False, default=str))
                print(f"        Fix: {c.fix_hint}")
    print()


def write_report(res: AuditResult, path: Path, elapsed: float) -> None:
    h = res.headline
    lines = [
        f"# Datenprüfung {res.since:%Y-%m} bis {res.periods[-1]:%Y-%m}" if res.periods
        else "# Datenprüfung",
        "",
        f"Erstellt {datetime.now():%Y-%m-%d %H:%M} mit `scripts/audit_data.py` "
        f"({elapsed / 60:.1f} min). Maßstab: offizielle FIDE-Liste "
        "(Spieler×Periode mit `num_games > 0`, auch heute inaktive Spieler).",
        "",
    ]
    if res.federations:
        lines += [f"Föderationen: {', '.join(res.federations)}", ""]
    lines += [
        "## Kopfzahlen",
        "",
        "| Kennzahl | Wert |",
        "|---|---:|",
        f"| Soll-Kombinationen | {_n(h['soll_combos'])} |",
    ]
    if "soll_players" in h:
        lines += [
            f"| Soll-Spieler | {_n(h['soll_players'])} |",
            f"| Spieler mind. einmal gescrapt | {_fmt_pct(h['pct_players_touched'])} |",
        ]
    lines += [
        f"| Perioden gescrapt | {_fmt_pct(h['pct_periods_scraped'])} |",
        f"| Perioden vollständig (Partienzahl = Liste) | {_fmt_pct(h['pct_periods_complete'])} |",
        f"| Partien vorhanden | {_fmt_pct(h['pct_games_found'])} "
        f"({_n(h['found_games'])} / {_n(h['listed_games'])}) |",
        f"| Kette Liste ↔ Σ K×Δ | {_chain(h)} |",
        f"| Harte / weiche Checks | {h['hard_checks']} / {h['soft_checks']} |",
        "",
    ]
    if res.skipped_periods:
        lines += [f"> ⚠ Ohne Referenzliste übersprungen: "
                  f"{', '.join(f'{p:%Y-%m}' for p in res.skipped_periods)}", ""]

    lines += ["## Checks", "", "| | Ebene | Check | Befunde | geprüft | Anteil |",
              "|---|---|---|---:|---:|---:|"]
    for c in res.checks:
        lines.append(f"| {MARK[c.severity]} | {c.layer} | `{c.id}` — {c.title} | "
                     f"{_n(c.n_findings)} | {_n(c.n_checked) if c.n_checked else '–'} | "
                     f"{_fmt_pct(c.pct) if c.n_checked else '–'} |")
    lines.append("")

    if res.by_year:
        lines += ["## Nach Jahr", "",
                  "| Jahr | Soll | gescrapt | fehlt | no_data/error | vollständig | Partien | "
                  "Kette ok | erklärt | unerklärt |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for y, v in res.by_year.items():
            soll = v.get("soll", 0)
            chain = v.get("chain_checked", 0)
            lines.append(
                f"| {y} | {_n(soll)} | {_fmt_pct(_p(v.get('ok', 0), soll))} | "
                f"{_n(v.get('missing', 0))} | "
                f"{_n(v.get('listed_no_data', 0) + v.get('listed_error', 0))} | "
                f"{_fmt_pct(_p(v.get('complete', 0), soll))} | "
                f"{_fmt_pct(_p(v.get('found_games', 0), v.get('listed_games', 0)))} | "
                f"{_fmt_pct(_p(v.get('chain_ok', 0), chain))} | "
                f"{_fmt_pct(_p(v.get('chain_explained', 0), chain))} | "
                f"{_fmt_pct(_p(v.get('chain_unexplained', 0), chain))} |")
        lines.append("")

    if res.by_federation:
        feds = sorted(res.by_federation.items(),
                      key=lambda kv: -kv[1].get("missing", 0))[:25]
        lines += ["## Föderationen mit den meisten offenen Perioden", "",
                  "| Föd. | Soll | fehlt | gescrapt | Partien | Spieler nie gescrapt |",
                  "|---|---:|---:|---:|---:|---:|"]
        for fed, v in feds:
            soll = v.get("soll", 0)
            lines.append(
                f"| {fed} | {_n(soll)} | {_n(v.get('missing', 0))} | "
                f"{_fmt_pct(_p(v.get('ok', 0), soll))} | "
                f"{_fmt_pct(_p(v.get('found_games', 0), v.get('listed_games', 0)))} | "
                f"{_n(v.get('players_never', 0))} / {_n(v.get('players', 0))} |")
        lines.append("")

    lines += ["## Befunde im Detail", ""]
    for c in res.checks:
        if c.severity not in (SOFT, HARD, INFO) or not c.n_findings:
            continue
        lines += [f"### {MARK[c.severity]} `{c.id}` — {c.title}", ""]
        if c.note:
            lines += [c.note, ""]
        lines += [f"**Fix:** {c.fix_hint}", ""]
        if c.worst_periods:
            lines += ["Schlechteste Perioden: " + ", ".join(
                f"{p[:7]} ({_fmt_pct(v)})" for p, v in c.worst_periods), ""]
        if c.by_year:
            lines += ["Nach Jahr: " + ", ".join(
                f"{y}: {_n(n)}" for y, n in sorted(c.by_year.items())), ""]
        if c.sample:
            keys = list(dict.fromkeys(k for s in c.sample for k in s))
            lines += ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
            for s in c.sample:
                lines.append("| " + " | ".join(
                    str(s.get(k, "")).replace("|", "/") for k in keys) + " |")
            lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _p(a, b) -> float:
    return 100.0 * a / b if b else 0.0


def export_csv(res: AuditResult, path: Path) -> int:
    rows = []
    for c in res.checks:
        for s in c.sample:
            rows.append({"check": c.id, "severity": c.severity,
                         "detail": json.dumps(s, ensure_ascii=False, default=str),
                         **{k: s.get(k) for k in ("period", "fide_id", "federation")}})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["check", "severity", "period",
                                           "fide_id", "federation", "detail"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="erste Periode, YYYY-MM")
    parser.add_argument("--until", help="letzte Periode, YYYY-MM (Default: Vormonat)")
    parser.add_argument("--layer", default="0,1,2,3",
                        help="Prüfebenen, kommagetrennt (Default 0,1,2,3)")
    parser.add_argument("--federation", metavar="FED[,FED...]",
                        help="Soll-Menge auf diese Föderationen einschränken")
    parser.add_argument("--sample", type=int, default=20,
                        help="Stichprobe je Befundart (Default 20)")
    parser.add_argument("--print-limit", type=int, default=3,
                        help="Stichprobenzeilen je Check auf der Konsole (Default 3)")
    parser.add_argument("--jobs", type=int, default=2,
                        help="parallele DB-Verbindungen (Default 2; die DB ist geteilt)")
    parser.add_argument("--chain-tolerance", type=float, default=DEFAULT_CHAIN_TOLERANCE)
    parser.add_argument("--chain-threshold", type=float, default=DEFAULT_CHAIN_THRESHOLD_PCT,
                        help="%% unerklärter Kettenfenster je Periode, ab dem hart (Default 2)")
    parser.add_argument("--formula-threshold", type=float,
                        default=DEFAULT_FORMULA_THRESHOLD_PCT,
                        help="%% Formel-Abweichungen je Periode, ab dem hart (Default 0,1)")
    parser.add_argument("--no-data-threshold", type=float, default=DEFAULT_NO_DATA_THRESHOLD_PCT,
                        help="%% no_data trotz Liste je Periode, ab dem hart (Default 1)")
    parser.add_argument("--fewer-threshold", type=float, default=DEFAULT_FEWER_THRESHOLD_PCT,
                        help="%% Kombos mit zu wenig Partien je Periode, ab dem hart (Default 3)")
    parser.add_argument("--jump", type=int, default=DEFAULT_JUMP)
    parser.add_argument("--no-integrity", action="store_true",
                        help="strukturelle Checks aus integrity.py auslassen")
    parser.add_argument("--report", metavar="FILE.md", help="Markdown-Bericht schreiben")
    parser.add_argument("--csv", metavar="FILE", help="Stichproben als CSV exportieren")
    parser.add_argument("--summary-only", action="store_true",
                        help="nur die Einzeilen-Zusammenfassung ausgeben (für Logs)")
    args = parser.parse_args()

    since = parse_period(args.since)
    until = parse_period(args.until) if args.until else None
    layers = {int(x) for x in args.layer.split(",") if x.strip()}
    feds = [f.strip().upper() for f in args.federation.split(",")] if args.federation else None

    def progress(done: int, total: int, p: date) -> None:
        if done == total or done % 6 == 0:
            logger.info("  %d/%d Perioden geprüft (zuletzt %s)", done, total, p.strftime("%Y-%m"))

    t0 = time.monotonic()
    try:
        res = run_audit(
            connect, since, until, layers=layers, federations=feds,
            sample=args.sample, jobs=args.jobs,
            chain_tolerance=args.chain_tolerance,
            chain_threshold_pct=args.chain_threshold,
            formula_threshold_pct=args.formula_threshold,
            no_data_threshold_pct=args.no_data_threshold,
            fewer_threshold_pct=args.fewer_threshold,
            jump=args.jump, with_integrity=not args.no_integrity,
            progress=progress,
        )
    except Exception:
        logger.exception("Audit abgebrochen")
        return 2
    elapsed = time.monotonic() - t0

    if args.summary_only:
        print(summary_line(res))
    else:
        print_scorecard(res, args.print_limit)
        print(f"  {summary_line(res)}  ({elapsed / 60:.1f} min)\n")

    if args.report:
        write_report(res, Path(args.report).expanduser(), elapsed)
        logger.info("Bericht geschrieben: %s", args.report)
    if args.csv:
        n = export_csv(res, Path(args.csv).expanduser())
        logger.info("%d Stichprobenzeilen exportiert nach %s", n, args.csv)

    return 1 if res.has_hard() else 0


if __name__ == "__main__":
    sys.exit(main())
