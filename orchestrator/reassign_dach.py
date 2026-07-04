"""
DACH-Gruppen (GER, SUI, AUT) von Residential auf den neuen
Datacenter-Thread dc_dach umstellen.

Betrifft nur Gruppen mit status='pending' (noch nicht gescrapt);
laufende/abgeschlossene Gruppen bleiben unangetastet.

Ausführen im Container:
  docker compose exec dashboard python3 -m orchestrator.reassign_dach
"""
from orchestrator.setup_db import connect

conn = connect()
cur = conn.cursor()

print('--- Vorher ---')
cur.execute(
    "SELECT federation, thread_affinity, status, count(*) FROM scrape_groups "
    "WHERE federation IN ('GER','SUI','AUT') "
    "GROUP BY federation, thread_affinity, status "
    "ORDER BY federation, thread_affinity, status"
)
for row in cur.fetchall():
    print(row)

cur.execute(
    "UPDATE scrape_groups SET thread_affinity='dc_dach' "
    "WHERE federation IN ('GER','SUI','AUT') AND status='pending' "
    "AND (thread_affinity IS NULL OR thread_affinity != 'dc_dach')"
)
print(f"\nUmgestellt auf dc_dach: {cur.rowcount} Gruppen")

print('\n--- Nachher ---')
cur.execute(
    "SELECT federation, thread_affinity, status, count(*) FROM scrape_groups "
    "WHERE federation IN ('GER','SUI','AUT') "
    "GROUP BY federation, thread_affinity, status "
    "ORDER BY federation, thread_affinity, status"
)
for row in cur.fetchall():
    print(row)

conn.close()
