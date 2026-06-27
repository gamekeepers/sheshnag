import asyncio
from typing import Dict, List


class SSEManager:
    """Per-batch event publisher/subscriber using in-memory asyncio queues."""

    def __init__(self):
        # batch_id -> list of asyncio.Queue (multiple subscribers possible)
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # batch_id -> list of buffered event strings for late subscribers
        self._buffer: Dict[str, List[str]] = {}

    def subscribe(self, batch_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        if batch_id not in self._subscribers:
            self._subscribers[batch_id] = []
        self._subscribers[batch_id].append(q)
        # Replay buffered events so late subscribers don't miss anything
        for event in self._buffer.get(batch_id, []):
            q.put_nowait(event)
        return q

    def unsubscribe(self, batch_id: str, queue: asyncio.Queue) -> None:
        if batch_id in self._subscribers:
            try:
                self._subscribers[batch_id].remove(queue)
            except ValueError:
                pass

    async def publish(self, batch_id: str, event_type: str, data: dict) -> None:
        """Send an SSE-formatted event to all subscribers of a batch."""
        import json as _json

        payload = f"event: {event_type}\ndata: {_json.dumps(data)}\n\n"

        # Buffer for late subscribers
        if batch_id not in self._buffer:
            self._buffer[batch_id] = []
        self._buffer[batch_id].append(payload)
        if len(self._buffer[batch_id]) > 50:
            self._buffer[batch_id] = self._buffer[batch_id][-20:]

        # Push to live subscribers
        for q in self._subscribers.get(batch_id, []):
            await q.put(payload)

    def cleanup(self, batch_id: str) -> None:
        self._subscribers.pop(batch_id, None)
        self._buffer.pop(batch_id, None)


sse_manager = SSEManager()
