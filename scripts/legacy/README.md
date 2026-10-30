# scripts/legacy — System A (eingefroren)

Kuratierte Gruppen `female_top`/`male_control` & Co. (`groups`-Tabelle +
`players.analysis_group`), siehe CLAUDE.md „Gruppen: zwei Systeme, nur eines
davon lebt". Nicht fortführen; die Skripte bleiben als Methoden-Dokumentation.

Seit Phase 4 des Vier-Blöcke-Umbaus liegen sie hier statt in `scripts/`:
`seed_players.py`, `extend_male_control.py`, die `run_*`/`chain_*`-Shells und
`setup_raspi.sh`. Aufruf aus dem Repo-Root, z. B.

```bash
python scripts/legacy/seed_players.py --group GRUPPENNAME
bash scripts/legacy/run_local_backfill.sh GRUPPENNAME 2012-08-01 2026-03-01
```

Der Listenparser (`load_players_from_file` u. a.) liegt in `core/fide_list.py`.
