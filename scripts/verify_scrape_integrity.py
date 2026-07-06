#!/usr/bin/env python3
"""CLI für die Integritätsprüfung (False-Positive-Erkennung) — report-only.

Deckt Widersprüche zwischen behauptetem Scrape-Status (scrape_periods,
orchestrator.scrape_groups) und tatsächlich vorhandenen Rohdaten
(game_results) auf. Verändert NIE Daten; pro Check wird eine
Reparatur-Empfehlung ausgegeben.

Usage:
    python scripts/verify_scrape_integrity.py                     # alle Checks
    python scripts/verify_scrape_integrity.py --check blocked_error_rows,orphan_games
    python scripts/verify_scrape_integrity.py --csv findings.csv --limit 50
    python scripts/verify_scrape_integrity.py --threshold-pct 5

Exit-Code 1, wenn harte Findings existieren (cron-tauglich).
"""

import argparse
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.integrity import CHECK_IDS, CHECKS, has_hard_findings, run_checks
from orchestrator.setup_db import connect

logger = logging.getLogger(__name__)


def print_report(results: dict, limit: int):
    total_hard = total_soft = 0
    print()
    print("=" * 64)
    print("  Integritätsprüfung: scrape_periods / scrape_groups ↔ Rohdaten")
    print("=" * 64)
    for check in CHECKS:
        if check.id not in results:
            continue
        findings = results[check.id]
        hard = sum(1 for f in findings if f["severity"] == "hard")
        soft = len(findings) - hard
        total_hard += hard
        total_soft += soft
        marker = "✗" if hard else ("△" if soft else "✓")
        print(f"\n  {marker} {check.id}: {len(findings)} Findings"
              f" ({hard} hart, {soft} weich)")
        print(f"    {check.description}")
        for f in findings[:limit]:
            extra = {k: v for k, v in f.items()
                     if k not in ("check", "severity", "subject")}
            detail = ", ".join(f"{k}={v}" for k, v in extra.items())
            print(f"      [{f['severity']:<4}] {f['subject']}  {detail}")
        if len(findings) > limit:
            print(f"      … {len(findings) - limit} weitere (--limit erhöhen oder --csv)")
        if findings:
            print(f"    Fix: {check.fix_hint}")

    print()
    print(f"  Gesamt: {total_hard} harte, {total_soft} weiche Findings")
    print()


def export_csv(results: dict, path: str):
    rows = [f for findings in results.values() for f in findings]
    if not rows:
        print("  Keine Findings — kein CSV geschrieben.")
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"  {len(rows)} Findings exportiert nach {path}")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", metavar="ID[,ID...]",
                        help=f"Nur diese Checks (verfügbar: {', '.join(CHECK_IDS)})")
    parser.add_argument("--csv", metavar="FILE", help="Findings als CSV exportieren")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max. Findings pro Check in der Konsole (default 20)")
    parser.add_argument("--threshold-pct", type=float, default=2.0,
                        help="Schwellwert fehlender Kombos für done_groups-Check "
                             "(default 2.0 — Rating-Drift-Toleranz)")
    args = parser.parse_args()

    check_ids = args.check.split(",") if args.check else None
    if check_ids:
        unknown = set(check_ids) - set(CHECK_IDS)
        if unknown:
            parser.error(f"Unbekannte Checks: {', '.join(sorted(unknown))}")

    conn = connect()
    try:
        logger.info("Führe Checks aus: %s", ", ".join(check_ids or CHECK_IDS))
        results = run_checks(conn, check_ids=check_ids,
                             threshold_pct=args.threshold_pct)
    finally:
        conn.close()

    print_report(results, limit=args.limit)
    if args.csv:
        export_csv(results, args.csv)

    sys.exit(1 if has_hard_findings(results) else 0)


if __name__ == "__main__":
    main()
