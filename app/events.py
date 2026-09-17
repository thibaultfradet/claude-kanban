from __future__ import annotations

import asyncio
import json
from typing import Any, Dict


class Broadcaster:
    """Simple in-memory pub/sub used to feed Server-Sent Events streams."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def publish(self, data: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            await queue.put(data)


# Board-level events: ticket created / status changed. Used to auto-refresh the board.
board_events = Broadcaster()

# Per-ticket live log events, keyed by ticket_id. Used for the "logs live" viewer.
_ticket_log_broadcasters: Dict[int, Broadcaster] = {}


def ticket_log_broadcaster(ticket_id: int) -> Broadcaster:
    if ticket_id not in _ticket_log_broadcasters:
        _ticket_log_broadcasters[ticket_id] = Broadcaster()
    return _ticket_log_broadcasters[ticket_id]


def sse_format(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"
