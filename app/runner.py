from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from sqlmodel import Session, select

from .db import LOGS_DIR, engine
from .events import ticket_log_broadcaster
from .models import EntryAuthor, EntryKind, Project, Ticket, TicketEntry, TicketStatus

CLAUDE_BIN = os.environ.get("KANBAN_CLAUDE_BIN", "claude")
# Opus is the CLI default and noticeably more expensive for routine tickets; sonnet
# is a saner default for unattended runs. Override with KANBAN_MODEL if needed.
MODEL = os.environ.get("KANBAN_MODEL", "sonnet")
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("KANBAN_TICKET_TIMEOUT", "1800"))

# These tools assume a human is present to answer; none is, in a headless run.
DISALLOWED_TOOLS = "AskUserQuestion,EnterPlanMode,ExitPlanMode"

KANBAN_SYSTEM_PROMPT = """\
Tu exécutes un ticket de façon totalement autonome, en mode headless (--print). \
Personne ne répondra pendant ce run : n'utilise jamais AskUserQuestion, \
EnterPlanMode ou ExitPlanMode (ces outils sont désactivés pour cette session). \
Si tu as besoin d'une clarification pour continuer, arrête-toi simplement en \
terminant ta réponse par le bloc structuré décrit plus bas avec status="question".

Règles de travail :
- Travaille sur la branche git courante du dépôt. Ne crée jamais de nouvelle branche.
- Commit fréquemment, par petites étapes logiques, au fur et à mesure de ton \
avancement. N'attends jamais la fin pour tout committer d'un coup. Ne pas hésiter \
à committer même par lots.
- Format des messages de commit, strictement : "<type>: <message concis en \
anglais>" avec type parmi feat, fix, chore, style. Une ligne, minuscules après le \
type, pas de point final. Jamais de mention de Claude/AI/outil, jamais de \
Co-authored-by.
- Ne fais jamais de `git push`.

Termine TOUJOURS ta dernière réponse par exactement ce bloc (rien après) :

<<<KANBAN_STATUS>>>
{"status": "done", "message": "résumé de ce qui a été fait"}
<<<END_KANBAN_STATUS>>>

ou, si tu es bloqué et as besoin d'une clarification humaine avant de continuer :

<<<KANBAN_STATUS>>>
{"status": "question", "message": "ta question précise"}
<<<END_KANBAN_STATUS>>>
"""

_STATUS_RE = re.compile(
    r"<<<KANBAN_STATUS>>>\s*(\{.*?\})\s*<<<END_KANBAN_STATUS>>>", re.DOTALL
)

_ENTRY_HEADERS = {
    EntryKind.plan_initial: "Plan initial",
    EntryKind.execution_summary: "Résumé d'exécution précédent",
    EntryKind.question: "Question posée par l'agent",
    EntryKind.answer: "Réponse de l'utilisateur",
    EntryKind.rejection: "Retour humain après test (rejet)",
    EntryKind.commit_info: "Info de commit",
    EntryKind.error: "Erreur",
}


@dataclass
class RunResult:
    session_id: Optional[str]
    final_text: Optional[str]
    is_error: bool
    total_cost_usd: Optional[float]
    timed_out: bool
    exit_code: Optional[int]


def build_ticket_prompt(entries: List[TicketEntry]) -> str:
    parts = []
    for entry in entries:
        header = _ENTRY_HEADERS.get(entry.kind, entry.kind)
        parts.append(f"## {header} ({entry.created_at.isoformat()})\n\n{entry.content}")
    return "\n\n---\n\n".join(parts)


def parse_kanban_status(final_text: Optional[str]) -> Optional[dict]:
    if not final_text:
        return None
    match = _STATUS_RE.search(final_text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def git_head_sha(project_path: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


async def _run_claude_headless(
    *,
    ticket_id: int,
    cwd: Path,
    prompt: str,
    session_flag: List[str],
    append_system_prompt: Optional[str] = None,
    disallowed_tools: Optional[str] = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    log_subdir: str = "run",
) -> RunResult:
    run_id = uuid.uuid4().hex[:8]
    log_dir = LOGS_DIR / str(ticket_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{log_subdir}-{run_id}.jsonl"

    cmd = [
        CLAUDE_BIN,
        "-p",
        *session_flag,
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]
    if MODEL:
        cmd += ["--model", MODEL]
    if append_system_prompt:
        cmd += ["--append-system-prompt", append_system_prompt]
    if disallowed_tools:
        cmd += ["--disallowedTools", disallowed_tools]
    cmd.append(prompt)

    broadcaster = ticket_log_broadcaster(ticket_id)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    state = {
        "session_id": None,
        "final_text": None,
        "is_error": False,
        "total_cost_usd": None,
    }

    async def _read_stdout() -> None:
        with log_path.open("a", encoding="utf-8") as log_file:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if not text:
                    continue
                log_file.write(text + "\n")
                log_file.flush()
                await broadcaster.publish({"type": "log", "line": text})
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "system" and event.get("subtype") == "init":
                    state["session_id"] = event.get("session_id") or state["session_id"]
                elif etype == "result":
                    state["session_id"] = event.get("session_id") or state["session_id"]
                    state["final_text"] = event.get("result")
                    state["is_error"] = bool(event.get("is_error"))
                    state["total_cost_usd"] = event.get("total_cost_usd")

    async def _read_stderr() -> None:
        with log_path.open("a", encoding="utf-8") as log_file:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                log_file.write("[stderr] " + text + "\n")
                await broadcaster.publish({"type": "stderr", "line": text})

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(_read_stdout(), _read_stderr(), proc.wait()),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()

    return RunResult(
        session_id=state["session_id"],
        final_text=state["final_text"],
        is_error=state["is_error"],
        total_cost_usd=state["total_cost_usd"],
        timed_out=timed_out,
        exit_code=proc.returncode,
    )


async def _apply_result(ticket_id: int, run: RunResult) -> None:
    from . import events  # local import to avoid a cycle at module load time

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            return

        if run.session_id:
            ticket.session_id = run.session_id

        status = None if (run.timed_out or run.is_error) else parse_kanban_status(run.final_text)

        if status is None:
            ticket.status = TicketStatus.standby
            reason = "timeout" if run.timed_out else ("erreur" if run.is_error else "réponse illisible")
            detail = run.final_text or "(aucune sortie finale)"
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.error,
                    author=EntryAuthor.agent,
                    content=f"Le run s'est arrêté sans statut exploitable ({reason}). Dernière sortie :\n\n{detail}",
                )
            )
        elif status.get("status") == "question":
            ticket.status = TicketStatus.standby
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.question,
                    author=EntryAuthor.agent,
                    content=str(status.get("message", "")),
                )
            )
        elif status.get("status") == "done":
            ticket.status = TicketStatus.a_valider
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.execution_summary,
                    author=EntryAuthor.agent,
                    content=str(status.get("message", "")),
                )
            )
        else:
            ticket.status = TicketStatus.standby
            session.add(
                TicketEntry(
                    ticket_id=ticket_id,
                    kind=EntryKind.error,
                    author=EntryAuthor.agent,
                    content=f"Statut inattendu renvoyé par l'agent : {status!r}",
                )
            )

        from datetime import datetime

        ticket.updated_at = datetime.utcnow()
        session.add(ticket)
        session.commit()
        new_status = ticket.status.value
        project_id = ticket.project_id

    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id, "status": new_status}
    )


async def execute_ticket_start(project_id: int, ticket_id: int) -> None:
    with Session(engine) as session:
        project = session.get(Project, project_id)
        ticket = session.get(Ticket, ticket_id)
        entries = session.exec(
            select(TicketEntry)
            .where(TicketEntry.ticket_id == ticket_id)
            .order_by(TicketEntry.created_at)
        ).all()

        prompt = build_ticket_prompt(entries)
        new_session_id = str(uuid.uuid4())
        ticket.session_id = new_session_id
        project_path = Path(project.path)
        if not ticket.head_sha_at_start:
            ticket.head_sha_at_start = git_head_sha(project_path)
        session.add(ticket)
        session.commit()

    run = await _run_claude_headless(
        ticket_id=ticket_id,
        cwd=project_path,
        prompt=prompt,
        session_flag=["--session-id", new_session_id],
        append_system_prompt=KANBAN_SYSTEM_PROMPT,
        disallowed_tools=DISALLOWED_TOOLS,
        log_subdir="start",
    )
    await _apply_result(ticket_id, run)


async def execute_ticket_resume(ticket_id: int, answer_text: str) -> None:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        project = session.get(Project, ticket.project_id)
        session_id = ticket.session_id
        project_path = Path(project.path)

    run = await _run_claude_headless(
        ticket_id=ticket_id,
        cwd=project_path,
        prompt=answer_text,
        session_flag=["--resume", session_id],
        append_system_prompt=KANBAN_SYSTEM_PROMPT,
        disallowed_tools=DISALLOWED_TOOLS,
        log_subdir="resume",
    )
    await _apply_result(ticket_id, run)
