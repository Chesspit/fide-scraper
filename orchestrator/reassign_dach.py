"""
DACH-Gruppen (GER, SUI, AUT) von Residential auf den neuen
Datacenter-Thread dc_dach umstellen.

Betrifft nur Gruppen mit status='pending' (noch nicht gescrapt);
laufende/abgeschlossene Gruppen bleiben unangetastet.

Ausführen im Container:
  docker compose exec dashboard python3 reassign_dach.py
"""
import sqlite3, os

db = os.environ.get('ORCHESTRATOR_DATA_DIR', '/data') + '/scraper.db'
conn = sqlite3.connect(db)

print('--- Vorher ---')
for row in conn.execute(
    "SELECT federation, thread_affinity, status, count(*) FROM scrape_groups "
    "WHERE federation IN ('GER','SUI','AUT') "
    "GROUP BY federation, thread_affinity, status "
    "ORDER BY federation, thread_affinity, status"
):
    print(row)

r = conn.execute(
    "UPDATE scrape_groups SET thread_affinity='dc_dach' "
    "WHERE federation IN ('GER','SUI','AUT') AND status='pending' "
    "AND (thread_affinity IS NULL OR thread_affinity != 'dc_dach')"
)
print(f"\nUmgestellt auf dc_dach: {r.rowcount} Gruppen")
conn.commit()

print('\n--- Nachher ---')
for row in conn.execute(
    "SELECT federation, thread_affinity, status, count(*) FROM scrape_groups "
    "WHERE federation IN ('GER','SUI','AUT') "
    "GROUP BY federation, thread_affinity, status "
    "ORDER BY federation, thread_affinity, status"
):
    print(row)

conn.close()
