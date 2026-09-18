from __future__ import annotations

import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from sqlmodel import Session

from . import events, runner
from .db import engine
from .models import EntryAuthor, EntryKind, Project, Ticket, TicketEntry, TicketStatus

COMMIT_SKILL_PATH = Path.home() / ".claude" / "commands" / "commit.md"

HEADLESS_OVERRIDE_NOTE = """

---

Note pour ce run headless (pas de terminal interactif) : aucune validation humaine
n'est possible ici. Choisis toi-même le découpage en lots le plus cohérent (Étape 2)
et exécute directement les étapes 3 et 4 sans attendre de confirmation. Toutes les
autres règles ci-dessus restent inchangées (pas de push, pas de co-authored, format
des messages, etc.).
"""


def _run_git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
    )


async def run_commit(project_id: int, ticket_id: int) -> None:
    with Session(engine) as session:
        project = session.get(Project, project_id)
        ticket = session.get(Ticket, ticket_id)
        project_path = Path(project.path)
        head_before = ticket.head_sha_at_start or runner.git_head_sha(project_path)

    try:
        commit_skill_text = COMMIT_SKILL_PATH.read_text(encoding="utf-8")
        prompt = commit_skill_text + HEADLESS_OVERRIDE_NOTE

        run = await runner.run_claude_headless(
            ticket_id=ticket_id,
            cwd=project_path,
            prompt=prompt,
            session_flag=["--session-id", str(uuid.uuid4())],
            disallowed_tools="AskUserQuestion,EnterPlanMode,ExitPlanMode",
            log_subdir="commit",
        )
    except Exception as exc:
        with Session(engine) as session:
            ticket = session.get(Ticket, ticket_id)
            ticket.error_message = f"Échec du lancement du commit automatique : {exc}"
            ticket.updated_at = datetime.utcnow()
            session.add(ticket)
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.error,
                    author=EntryAuthor.agent,
                    content=ticket.error_message,
                )
            )
            session.commit()
            project_id_out = ticket.project_id
            status_out = ticket.status.value
        await events.board_events.publish(
            {"ticket_id": ticket_id, "project_id": project_id_out, "status": status_out}
        )
        return

    head_after = runner.git_head_sha(project_path)

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)

        if run.timed_out or run.is_error:
            ticket.error_message = (
                "Le run de commit automatique a échoué ou dépassé le délai. "
                "Vérifie l'état du dépôt manuellement puis réessaie."
            )
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.error,
                    author=EntryAuthor.agent,
                    content=ticket.error_message
                    + f"\n\nDernière sortie :\n\n{run.final_text or '(aucune)'}",
                )
            )
        else:
            ticket.error_message = None
            ticket.commit_sha = head_after
            if head_before and head_after and head_before != head_after:
                log = _run_git(
                    ["log", "--oneline", f"{head_before}..{head_after}"], project_path
                )
                commit_message = log.stdout.strip() or "(historique indisponible)"
            else:
                commit_message = (
                    "Aucun nouveau commit à ce stade (déjà tout committé pendant "
                    "l'exécution du ticket)."
                )
            ticket.commit_message = commit_message
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.commit_info,
                    author=EntryAuthor.agent,
                    content=commit_message,
                )
            )

        ticket.updated_at = datetime.utcnow()
        session.add(ticket)
        session.commit()
        project_id_out = ticket.project_id
        status_out = ticket.status.value

    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id_out, "status": status_out}
    )


async def push_ticket(project_id: int, ticket_id: int) -> Tuple[bool, str]:
    with Session(engine) as session:
        project = session.get(Project, project_id)
        project_path = Path(project.path)

    branch_res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], project_path)
    branch_name = branch_res.stdout.strip()
    if branch_res.returncode != 0 or not branch_name:
        return False, "Impossible de déterminer la branche git courante."

    has_upstream = _run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], project_path
    )
    if has_upstream.returncode == 0:
        push_res = _run_git(["push"], project_path)
    else:
        push_res = _run_git(["push", "-u", "origin", branch_name], project_path)

    if push_res.returncode != 0:
        return False, (push_res.stderr.strip() or "Échec du push (raison inconnue).")

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        ticket.status = TicketStatus.termine
        ticket.updated_at = datetime.utcnow()
        session.add(ticket)
        session.add(
            TicketEntry(
                ticket_id=ticket_id,
                kind=EntryKind.commit_info,
                author=EntryAuthor.user,
                content=f"Poussé sur `{branch_name}`.",
            )
        )
        session.commit()
        project_id_out = ticket.project_id

    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id_out, "status": TicketStatus.termine.value}
    )
    return True, f"Poussé sur {branch_name}."
