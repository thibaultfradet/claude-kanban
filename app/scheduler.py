from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from sqlmodel import Session, select

from . import events, runner
from .db import engine
from .models import OCCUPIED_STATUSES, Project, Ticket, TicketStatus

POLL_INTERVAL_SECONDS = int(os.environ.get("KANBAN_POLL_INTERVAL", "20"))

logger = logging.getLogger("kanban.scheduler")


async def scheduler_loop() -> None:
    while True:
        try:
            await tick()
        except Exception:  # pragma: no cover - keep the loop alive no matter what
            logger.exception("scheduler tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def tick() -> None:
    to_launch = []
    with Session(engine) as session:
        projects = session.exec(select(Project)).all()
        for project in projects:
            occupied = session.exec(
                select(Ticket).where(
                    Ticket.project_id == project.id,
                    Ticket.status.in_(OCCUPIED_STATUSES),
                )
            ).first()
            if occupied is not None:
                continue

            next_ticket = session.exec(
                select(Ticket)
                .where(Ticket.project_id == project.id, Ticket.status == TicketStatus.a_faire)
                .order_by(Ticket.created_at)
            ).first()
            if next_ticket is None:
                continue

            next_ticket.status = TicketStatus.en_cours
            next_ticket.updated_at = datetime.utcnow()
            session.add(next_ticket)
            to_launch.append((project.id, next_ticket.id))

        session.commit()

    for project_id, ticket_id in to_launch:
        asyncio.create_task(runner.execute_ticket_start(project_id, ticket_id))
        await events.board_events.publish(
            {"ticket_id": ticket_id, "project_id": project_id, "status": TicketStatus.en_cours.value}
        )
