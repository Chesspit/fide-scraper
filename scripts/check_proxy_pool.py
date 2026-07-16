#!/usr/bin/env python3
"""Test every IP:PORT in the proxy pool file against ratings.fide.com.

Historisch der Webshare-Pool-Health-Check (100 statische IPs, tote Einträge
als Kandidaten für Webshares "replace 10 dead IPs"-Option). Seit der
DataImpulse-Migration (2026-07-16) enthält die Pool-Datei nur noch das
rotierende Gateway — als Health-Check obsolet, aber weiter nützlich als
Smoke-Test: mit --repeat N gehen N Requests durchs Gateway (jede über eine
andere Residential-IP). Run this on the VPS (network path matters) via:

    docker compose exec -T worker python3 scripts/check_proxy_pool.py --repeat 5

Usage:
    python3 scripts/check_proxy_pool.py [--pool-file PATH] [--timeout SECONDS] [--repeat N]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from orchestrator.proxy_manager import ProxyManager
from scraper.fetcher import AJAX_URL, HEADERS, REFERER_URL

# Known-good test target (Gukesh D) — small, fast response either way.
TEST_FIDE_ID = 46616543
TEST_PERIOD = "2026-06-01"


def main() -> int:
    parser = argparse.ArgumentParser(description="Health-check every proxy in the pool")
    parser.add_argument("--pool-file", default=None, help="Override PROXY_POOL_FILE")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--repeat", type=int, default=1,
                        help="Jeden Pool-Eintrag N-mal testen (Smoke-Test fürs rotierende Gateway)")
    args = parser.parse_args()

    pm = ProxyManager(pool_file=args.pool_file)
    if not pm._pool:
        print("Kein Pool geladen — PROXY_POOL_FILE / --pool-file prüfen.")
        return 1

    url = AJAX_URL.format(fide_id=TEST_FIDE_ID, period=TEST_PERIOD)
    headers = {**HEADERS, "Referer": REFERER_URL.format(fide_id=TEST_FIDE_ID, period=TEST_PERIOD)}

    entries = pm._pool * max(1, args.repeat)
    print(f"Teste {len(pm._pool)} Proxies ({len(entries)} Requests) ...")
    dead: list[str] = []
    ok = 0

    for host, port in entries:
        proxy_url = f"http://{pm._user}:{pm._pw}@{host}:{port}"
        proxies = {"http": proxy_url, "https": proxy_url}
        label = f"{host}:{port}"
        start = time.time()
        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=args.timeout)
            elapsed = time.time() - start
            if resp.status_code == 200 and len(resp.text) > 0:
                print(f"  OK    {label:22s} {elapsed:5.1f}s  HTTP {resp.status_code}  {len(resp.text)}B")
                ok += 1
            else:
                print(f"  WARN  {label:22s} {elapsed:5.1f}s  HTTP {resp.status_code}  leer/unerwartet")
                dead.append(label)
        except requests.RequestException as exc:
            elapsed = time.time() - start
            print(f"  FAIL  {label:22s} {elapsed:5.1f}s  {type(exc).__name__}")
            dead.append(label)

    print(f"\nErgebnis: {ok}/{len(entries)} erreichbar, {len(dead)} tot/unerreichbar")
    if dead:
        print("\nTote/unerreichbare IPs (Kandidaten für Webshare-Ersatz, max. 10 pro Anfrage):")
        for label in dead[:10]:
            print(f"  {label}")
        if len(dead) > 10:
            print(f"  ... und {len(dead) - 10} weitere (Webshare erlaubt nur 10 auf einmal ersetzen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
