import asyncio
import logging
import uuid
from typing import AsyncIterator

logger = logging.getLogger("app.notifications.broadcaster")


class NotificationBroadcaster:
    """Tenant-isolated, in-memory real-time event broadcaster for SSE."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    def register(self, business_id: uuid.UUID) -> tuple[asyncio.Queue, callable]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        if business_id not in self._subscribers:
            self._subscribers[business_id] = set()
        self._subscribers[business_id].add(queue)

        def unsubscribe():
            if business_id in self._subscribers:
                self._subscribers[business_id].discard(queue)
                if not self._subscribers[business_id]:
                    del self._subscribers[business_id]

        return queue, unsubscribe

    async def subscribe(self, business_id: uuid.UUID) -> AsyncIterator[dict]:
        queue, unsubscribe = self.register(business_id)
        try:
            while True:
                item = await queue.get()
                yield item
        finally:
            unsubscribe()

    async def broadcast(self, business_id: uuid.UUID, data: dict) -> int:
        """Pushes data to all active queues for the specified business."""
        count = 0
        queues = list(self._subscribers.get(business_id, []))
        for queue in queues:
            try:
                queue.put_nowait(data)
                count += 1
            except asyncio.QueueFull:
                logger.warning(f"[Broadcaster] Queue full for business {business_id}, dropping message")
            except Exception as e:
                logger.warning(f"[Broadcaster] Failed to enqueue event: {e}")

        return count


# Singleton instance shared across the application process
broadcaster = NotificationBroadcaster()
