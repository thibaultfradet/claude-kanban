from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

from . import events, git_ops, runner, scheduler
from .db import engine, init_db
from .models import EntryAuthor, EntryKind, Project, Ticket, TicketEntry, TicketStatus

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

STATUS_LABELS = {
    TicketStatus.a_faire: "À faire",
    TicketStatus.en_cours: "En cours",
    TicketStatus.standby: "Stand by (question)",
    TicketStatus.a_valider: "À valider",
    TicketStatus.a_committer: "À committer",
    TicketStatus.termine: "Terminé",
}

STATUS_ORDER = [
    TicketStatus.a_faire,
    TicketStatus.en_cours,
    TicketStatus.standby,
    TicketStatus.a_valider,
    TicketStatus.a_committer,
    TicketStatus.termine,
]

ALLOWED_MANUAL_TRANSITIONS = {
    (TicketStatus.a_valider, TicketStatus.a_committer),
    (TicketStatus.a_committer, TicketStatus.a_valider),
}

_scheduler_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler_task
    init_db()
    await scheduler.recover_stale_tickets()
    _scheduler_task = asyncio.create_task(scheduler.scheduler_loop())
    yield
    if _scheduler_task:
        _scheduler_task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
async def board_page(request: Request):
    return templates.TemplateResponse("board.html", {"request": request})


@app.get("/fragments/board")
async def board_fragment(request: Request):
    with Session(engine) as session:
        tickets = session.exec(select(Ticket)).all()
        projects = {p.id: p for p in session.exec(select(Project)).all()}

    tickets_by_status = {status: [] for status in STATUS_ORDER}
    for ticket in tickets:
        tickets_by_status[ticket.status].append(ticket)
    for bucket in tickets_by_status.values():
        bucket.sort(key=lambda t: t.created_at)

    return templates.TemplateResponse(
        "board_fragment.html",
        {
            "request": request,
            "statuses": STATUS_ORDER,
            "status_labels": STATUS_LABELS,
            "tickets_by_status": tickets_by_status,
            "project_names": {pid: p.name for pid, p in projects.items()},
        },
    )


def _ticket_context(ticket_id: int) -> dict:
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        project = session.get(Project, ticket.project_id)
        entries = session.exec(
            select(TicketEntry)
            .where(TicketEntry.ticket_id == ticket_id)
            .order_by(TicketEntry.created_at)
        ).all()

    return {
        "ticket": ticket,
        "project": project,
        "entries": entries,
        "status_labels": STATUS_LABELS,
    }


@app.get("/tickets/{ticket_id}")
async def ticket_detail_page(request: Request, ticket_id: int):
    context = _ticket_context(ticket_id)
    context["request"] = request
    return templates.TemplateResponse("ticket_detail.html", context)


@app.get("/fragments/tickets/{ticket_id}")
async def ticket_detail_fragment(request: Request, ticket_id: int):
    context = _ticket_context(ticket_id)
    context["request"] = request
    return templates.TemplateResponse("_ticket_fragment.html", context)


# ---------------------------------------------------------------------------
# API — used by the kanban-ticket skill and the web UI
# ---------------------------------------------------------------------------


class CreateTicketBody(BaseModel):
    project_name: str
    project_path: str
    title: str
    plan: str


@app.post("/api/tickets")
async def create_ticket(body: CreateTicketBody):
    project_path = str(Path(body.project_path).expanduser().resolve())

    with Session(engine) as session:
        project = session.exec(select(Project).where(Project.path == project_path)).first()
        if project is None:
            project = Project(name=body.project_name, path=project_path)
            session.add(project)
            session.commit()
            session.refresh(project)

        ticket = Ticket(project_id=project.id, title=body.title, status=TicketStatus.a_faire)
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

        session.add(
            TicketEntry(
                ticket_id=ticket.id,
                kind=EntryKind.plan_initial,
                author=EntryAuthor.user,
                content=body.plan,
            )
        )
        session.commit()
        ticket_id = ticket.id
        project_id = project.id

    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id, "status": TicketStatus.a_faire.value}
    )
    return {"ticket_id": ticket_id, "project_id": project_id}


class FeedbackBody(BaseModel):
    message: str


@app.post("/api/tickets/{ticket_id}/feedback")
async def ticket_feedback(ticket_id: int, body: FeedbackBody):
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        ticket.status = TicketStatus.a_faire
        ticket.updated_at = datetime.utcnow()
        session.add(ticket)
        session.add(
            TicketEntry(
                ticket_id=ticket_id,
                kind=EntryKind.rejection,
                author=EntryAuthor.user,
                content=body.message,
            )
        )
        session.commit()
        project_id = ticket.project_id

    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id, "status": TicketStatus.a_faire.value}
    )
    return {"ok": True}


class AnswerBody(BaseModel):
    message: str


@app.post("/api/tickets/{ticket_id}/answer")
async def ticket_answer(ticket_id: int, body: AnswerBody):
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        if ticket.status != TicketStatus.standby:
            raise HTTPException(status_code=409, detail="Ce ticket n'est pas en stand by")
        ticket.status = TicketStatus.en_cours
        ticket.updated_at = datetime.utcnow()
        session.add(ticket)
        session.add(
            TicketEntry(
                ticket_id=ticket_id,
                kind=EntryKind.answer,
                author=EntryAuthor.user,
                content=body.message,
            )
        )
        session.commit()
        project_id = ticket.project_id

    asyncio.create_task(runner.execute_ticket_resume(ticket_id, body.message))
    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id, "status": TicketStatus.en_cours.value}
    )
    return {"ok": True}


class StatusBody(BaseModel):
    status: str


@app.patch("/api/tickets/{ticket_id}/status")
async def ticket_set_status(ticket_id: int, body: StatusBody):
    try:
        new_status = TicketStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Statut inconnu")

    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        if (ticket.status, new_status) not in ALLOWED_MANUAL_TRANSITIONS:
            raise HTTPException(status_code=409, detail="Transition non autorisée")

        ticket.status = new_status
        ticket.updated_at = datetime.utcnow()
        if new_status == TicketStatus.a_committer:
            ticket.error_message = None
            ticket.commit_message = None
        session.add(ticket)
        session.commit()
        project_id = ticket.project_id

    if new_status == TicketStatus.a_committer:
        asyncio.create_task(git_ops.run_commit(project_id, ticket_id))

    await events.board_events.publish(
        {"ticket_id": ticket_id, "project_id": project_id, "status": new_status.value}
    )
    return {"ok": True}


@app.post("/api/tickets/{ticket_id}/push")
async def ticket_push(ticket_id: int):
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket introuvable")
        if ticket.status != TicketStatus.a_committer:
            raise HTTPException(status_code=409, detail="Ce ticket n'est pas prêt à être poussé")
        project_id = ticket.project_id

    ok, message = await git_ops.push_ticket(project_id, ticket_id)
    return {"ok": ok, "message": message}


@app.get("/api/tickets/{ticket_id}/stream")
async def ticket_log_stream(ticket_id: int):
    broadcaster = events.ticket_log_broadcaster(ticket_id)
    queue = broadcaster.subscribe()

    async def gen():
        try:
            while True:
                data = await queue.get()
                yield events.sse_format(data)
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/events")
async def board_event_stream():
    queue = events.board_events.subscribe()

    async def gen():
        try:
            while True:
                data = await queue.get()
                yield events.sse_format(data)
        finally:
            events.board_events.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
