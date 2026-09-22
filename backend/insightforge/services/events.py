import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any


class RunEventBus:
    def __init__(self, loop: asyncio.AbstractEventLoop | None = None):
        self.loop = loop or asyncio.get_event_loop()
        self.queues: dict[str, asyncio.Queue[dict[str, Any]]] = defaultdict(asyncio.Queue)
        self.buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.terminal: set[str] = set()

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        self.loop.call_soon_threadsafe(self._put, run_id, payload)

    def _put(self, run_id: str, event: dict[str, Any]) -> None:
        self.buffers[run_id].append(event)
        self.queues[run_id].put_nowait(event)
        if event.get("type") in {"done", "error"}:
            self.terminal.add(run_id)

    async def subscribe(self, run_id: str) -> AsyncGenerator[dict[str, Any], None]:
        buffered = list(self.buffers.get(run_id, []))
        for event in buffered:
            yield event
            if event.get("type") in {"done", "error"}:
                return
        while True:
            event = await self.queues[run_id].get()
            yield event
            if event.get("type") in {"done", "error"}:
                return

    def finish(self, run_id: str) -> None:
        self.terminal.add(run_id)
        self.loop.call_later(600, self._drop, run_id)

    def _drop(self, run_id: str) -> None:
        self.buffers.pop(run_id, None)
        self.queues.pop(run_id, None)
        self.terminal.discard(run_id)
