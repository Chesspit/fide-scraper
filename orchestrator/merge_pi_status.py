#!/usr/bin/env python3
"""Merge Pi scrape_runs into VPS orchestrator DB and sync raspi group statuses.

Usage (inside orchestrator-worker-1 on VPS):
    python3 /app/orchestrator/merge_pi_status.py \
        [--pi-db /tmp/scraper_pi.db] [--vps-db /data/scraper.db] [--dry-run]

Pi-Gruppen haben neue Auto-Increment-IDs (durch export_pi_groups.py).
Join erfolgt über Natural Key (federation, year, elo_min).
Neue scrape_runs werden mit thread_slot=50 ("Pi") in die VPS-DB eingefügt.
"""
import argparse
import sqlite3

PI_THREAD_SLOT = 50


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi-db",  default="/tmp/scraper_pi.db")
    parser.add_argument("--vps-db", default="/data/scraper.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pi  = _connect(args.pi_db)
    vps = _connect(args.vps_db)

    # 1. Natural-key → VPS group_id
    key_to_vps_id = {
        (r["federation"], r["year"], r["elo_min"]): r["id"]
        for r in vps.execute(
            "SELECT id, federation, year, elo_min FROM scrape_groups WHERE device='raspi'"
        )
    }
    print(f"VPS raspi groups: {len(key_to_vps_id)}")

    # 2. Watermark: letzter bereits gemergter Pi-Run in VPS
    watermark: str = vps.execute(
        "SELECT COALESCE(MAX(started_at),'1970-01-01T00:00:00') "
        "FROM scrape_runs WHERE thread_slot=?",
        (PI_THREAD_SLOT,),
    ).fetchone()[0]
    print(f"Watermark (letzter Merge): {watermark}")

    # 3. Neue Pi-Runs (nach Watermark)
    pi_runs = pi.execute(
        """SELECT r.started_at, r.finished_at, r.status, r.records_found,
                  r.error_msg, r.proxy_used, r.profile_used, r.mb_downloaded,
                  g.federation, g.year, g.elo_min
           FROM   scrape_runs r
           JOIN   scrape_groups g ON g.id = r.group_id
           WHERE  r.started_at > ? AND r.started_at IS NOT NULL
           ORDER  BY r.started_at ASC""",
        (watermark,),
    ).fetchall()
    print(f"Neue Pi-Runs zum Mergen: {len(pi_runs)}")

    # 4. Insert in VPS scrape_runs mit thread_slot=50
    inserted = skipped = 0
    for run in pi_runs:
        key = (run["federation"], run["year"], run["elo_min"])
        vps_gid = key_to_vps_id.get(key)
        if vps_gid is None:
            print(f"  SKIP (kein VPS-Eintrag): {key}")
            skipped += 1
            continue
        if not args.dry_run:
            vps.execute(
                """INSERT INTO scrape_runs
                   (group_id, started_at, finished_at, status, records_found,
                    error_msg, proxy_used, profile_used, mb_downloaded, thread_slot)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    vps_gid,
                    run["started_at"], run["finished_at"], run["status"],
                    run["records_found"] or 0,
                    (run["error_msg"] or "")[:500],
                    run["proxy_used"] or "",
                    run["profile_used"] or "",
                    run["mb_downloaded"] or 0.0,
                    PI_THREAD_SLOT,
                ),
            )
        inserted += 1

    # 5. Gruppen-Status sync: pending/running/done in VPS aktualisieren
    updated = 0
    for pg in pi.execute(
        "SELECT federation, year, elo_min, status, last_run_at, records_found "
        "FROM scrape_groups"
    ):
        vps_gid = key_to_vps_id.get((pg["federation"], pg["year"], pg["elo_min"]))
        if not vps_gid:
            continue
        if not args.dry_run:
            vps.execute(
                "UPDATE scrape_groups "
                "SET status=?, last_run_at=?, records_found=? "
                "WHERE id=? AND device='raspi'",
                (pg["status"], pg["last_run_at"], pg["records_found"], vps_gid),
            )
        updated += 1

    if not args.dry_run:
        vps.commit()

    print(f"\nRuns inserted:   {inserted}")
    print(f"Runs skipped:    {skipped}")
    print(f"Groups updated:  {updated}")
    if args.dry_run:
        print("\n[dry-run] Keine Änderungen geschrieben.")

    pi.close()
    vps.close()


if __name__ == "__main__":
    main()
