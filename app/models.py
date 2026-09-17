from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TicketStatus(str, enum.Enum):
    a_faire = "a_faire"
    en_cours = "en_cours"
    standby = "standby"
    a_valider = "a_valider"
    a_committer = "a_committer"
    termine = "termine"


# Statuses that mean "this project's working directory is occupied" — no worktree
# isolation, so only one ticket per project may be anywhere in this range at a time.
OCCUPIED_STATUSES = (
    TicketStatus.en_cours,
    TicketStatus.standby,
    TicketStatus.a_valider,
    TicketStatus.a_committer,
)


class EntryKind(str, enum.Enum):
    plan_initial = "plan_initial"
    execution_summary = "execution_summary"
    question = "question"
    answer = "answer"
    rejection = "rejection"
    commit_info = "commit_info"
    error = "error"


class EntryAuthor(str, enum.Enum):
    user = "user"
    agent = "agent"


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    path: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str
    status: TicketStatus = Field(default=TicketStatus.a_faire, index=True)
    session_id: Optional[str] = None
    head_sha_at_start: Optional[str] = None
    commit_sha: Optional[str] = None
    commit_message: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TicketEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    kind: EntryKind
    author: EntryAuthor
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
