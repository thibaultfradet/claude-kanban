#!/usr/bin/env python3
"""Auto-ship : valide, committe et pousse automatiquement les tickets terminés.

Boucle externe au tableau (aucune modification de l'appli) :
  1. « À valider »  -> « À committer » (le tableau lance alors son commit automatique)
  2. commit réussi  -> push (le ticket passe en « Terminé »)
  3. échec du commit ou du push -> journalisé, laissé à la main (pas de boucle infinie)

Lancer :  python3 scripts/auto_ship.py           (tous les projets)
          python3 scripts/auto_ship.py mailac   (seulement les projets nommés)
Arrêter : Ctrl-C
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BOARD = "http://127.0.0.1:8787"
DB = Path(__file__).resolve().parent.parent / "data" / "kanban.db"
POLL_SECONDS = 15
MAX_VALIDATIONS = 2  # par ticket : évite de re-valider sans fin un ticket renvoyé en « À valider »
MAX_PUSH_TRIES = 3
PUSH_RETRY_SECONDS = 120

only_projects = set(sys.argv[1:])
validations: dict[int, int] = {}
push_tries: dict[int, int] = {}
last_push_try: dict[int, float] = {}
reported: set[tuple[int, str]] = set()


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def report_once(ticket_id: int, key: str, msg: str) -> None:
    if (ticket_id, key) not in reported:
        reported.add((ticket_id, key))
        log(msg)


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BOARD + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def tickets() -> list[sqlite3.Row]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "select t.id, t.title, t.status, t.commit_message, t.error_message, p.name as project "
            "from ticket t join project p on p.id = t.project_id "
            "where t.status in ('a_valider', 'a_committer') order by t.id"
        ).fetchall()
    finally:
        con.close()


def tick() -> None:
    for t in tickets():
        if only_projects and t["project"] not in only_projects:
            continue
        tid, name = t["id"], f"{t['project']} · {t['title']}"

        if t["status"] == "a_valider":
            if validations.get(tid, 0) >= MAX_VALIDATIONS:
                report_once(tid, "max-valid", f"⏸  {name} : revenu en « À valider » plusieurs fois, je le laisse à ta relecture.")
                continue
            code, res = api("PATCH", f"/api/tickets/{tid}/status", {"status": "a_committer"})
            if code == 200:
                validations[tid] = validations.get(tid, 0) + 1
                log(f"✔  validé, commit automatique lancé : {name}")
            else:
                report_once(tid, f"valid-{code}", f"✖  validation refusée ({code} {res.get('detail', '')}) : {name}")

        elif t["status"] == "a_committer":
            if t["error_message"]:
                report_once(tid, "commit-err", f"✖  commit échoué, à reprendre à la main : {name}\n     {t['error_message']}")
                continue
            if not t["commit_message"]:
                continue  # le commit automatique tourne encore
            if push_tries.get(tid, 0) >= MAX_PUSH_TRIES:
                report_once(tid, "max-push", f"⏸  push abandonné après {MAX_PUSH_TRIES} essais : {name}")
                continue
            if time.time() - last_push_try.get(tid, 0) < PUSH_RETRY_SECONDS:
                continue
            push_tries[tid] = push_tries.get(tid, 0) + 1
            last_push_try[tid] = time.time()
            code, res = api("POST", f"/api/tickets/{tid}/push")
            if code == 200 and res.get("ok"):
                log(f"⬆  poussé, ticket terminé : {name}")
            else:
                log(f"✖  push échoué (essai {push_tries[tid]}/{MAX_PUSH_TRIES}) : {name}\n     {res.get('message') or res.get('detail')}")


def main() -> None:
    scope = ", ".join(sorted(only_projects)) or "tous les projets"
    log(f"auto-ship démarré ({scope}), vérification toutes les {POLL_SECONDS} s. Ctrl-C pour arrêter.")
    while True:
        try:
            tick()
        except (urllib.error.URLError, sqlite3.Error, ConnectionError) as e:
            report_once(0, f"down-{type(e).__name__}", f"…  tableau injoignable ({e}), je réessaie.")
        else:
            reported.discard((0, "down-URLError"))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("auto-ship arrêté.")
