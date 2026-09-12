import asyncio
import logging
import uuid
from collections.abc import Callable

logger = logging.getLogger("app.notifications.broadcaster")


class NotificationBroadcaster:
    """Tenant-isolated, in-memory real-time event broadcaster for SSE."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    def register(self, business_id: uuid.UUID) -> tuple[asyncio.Queue, Callable[[], None]]:
        """Returns (queue, unsubscribe). The caller must call unsubscribe when
        its stream ends, or the queue leaks and keeps receiving events."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(business_id, set()).add(queue)

        def unsubscribe() -> None:
            subscribers = self._subscribers.get(business_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    del self._subscribers[business_id]

        return queue, unsubscribe

    def subscriber_count(self, business_id: uuid.UUID) -> int:
        return len(self._subscribers.get(business_id, ()))

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
