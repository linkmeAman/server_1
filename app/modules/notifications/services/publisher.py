"""Process-local notification publisher and SSE fanout broker.

The broker is intentionally isolated behind a small API so a Redis pub/sub or
database-backed implementation can replace the process-local queues without
changing route or domain code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from app.modules.notifications.schemas.models import NotificationEvent

logger = logging.getLogger(__name__)

PersistenceHook = Callable[[NotificationEvent], Awaitable[None] | None]

_SEVERITY_RANK = {
    "info": 10,
    "success": 20,
    "warning": 30,
    "error": 40,
    "critical": 50,
}


class NotificationBroker:
    def __init__(self, *, max_recent: int = 500, queue_size: int = 200) -> None:
        self._max_recent = max_recent
        self._queue_size = queue_size
        self._recent: deque[NotificationEvent] = deque(maxlen=max_recent)
        self._subscribers: dict[str, asyncio.Queue[NotificationEvent | None]] = {}
        self._lock = asyncio.Lock()
        self._persistence_hooks: list[PersistenceHook] = []

    def register_persistence_hook(self, hook: PersistenceHook) -> None:
        self._persistence_hooks.append(hook)

    async def publish(self, event: NotificationEvent) -> NotificationEvent:
        async with self._lock:
            self._recent.appendleft(event)
            subscribers = list(self._subscribers.items())

        for hook in self._persistence_hooks:
            try:
                result = hook(event)
                if result is not None:
                    await result
            except Exception:
                logger.exception(
                    "Notification persistence hook failed event_id=%s request_id=%s",
                    event.event_id,
                    event.request_id,
                )

        dropped = 0
        for _, queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.warning(
                "Dropped notification for %d slow subscribers event_id=%s request_id=%s",
                dropped,
                event.event_id,
                event.request_id,
            )

        logger.info(
            "NOTIFICATION_PUBLISHED event_id=%s request_id=%s event_type=%s severity=%s source=%s",
            event.event_id,
            event.request_id,
            event.event_type,
            event.severity,
            event.source,
        )
        return event

    async def recent(
        self,
        *,
        user_id: int | str | None = None,
        min_severity: str | None = None,
        limit: int = 100,
    ) -> list[NotificationEvent]:
        min_rank = _SEVERITY_RANK.get(min_severity or "info", 10)
        async with self._lock:
            events = list(self._recent)

        filtered = [
            event
            for event in events
            if (event.user_id in (None, user_id))
            and _SEVERITY_RANK.get(event.severity, 10) >= min_rank
        ]
        return filtered[: max(1, min(limit, self._max_recent))]

    async def subscribe(
        self,
        *,
        user_id: int | str | None = None,
        last_event_id: str | None = None,
    ) -> AsyncIterator[NotificationEvent | None]:
        queue: asyncio.Queue[NotificationEvent | None] = asyncio.Queue(maxsize=self._queue_size)
        subscriber_id = f"{id(queue)}"

        async with self._lock:
            self._subscribers[subscriber_id] = queue
            replay = self._replay_locked(user_id=user_id, last_event_id=last_event_id)

        try:
            for event in replay:
                yield event

            while True:
                event = await queue.get()
                if event is not None and event.user_id not in (None, user_id):
                    continue
                yield event
        finally:
            async with self._lock:
                self._subscribers.pop(subscriber_id, None)

    def _replay_locked(
        self,
        *,
        user_id: int | str | None,
        last_event_id: str | None,
    ) -> list[NotificationEvent]:
        if not last_event_id:
            return []

        replay: list[NotificationEvent] = []
        for event in self._recent:
            if event.event_id == last_event_id:
                break
            if event.user_id in (None, user_id):
                replay.append(event)
        replay.reverse()
        return replay


notification_broker = NotificationBroker()


async def publish_notification(
    *,
    request_id: str,
    event_type: str,
    severity: str = "info",
    source: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    user_id: int | str | None = None,
    group_key: str | None = None,
    dedupe_key: str | None = None,
) -> NotificationEvent:
    event = NotificationEvent(
        request_id=request_id,
        event_type=event_type,
        severity=severity,  # type: ignore[arg-type]
        source=source,
        message=message,
        metadata=metadata or {},
        user_id=user_id,
        group_key=group_key,
        dedupe_key=dedupe_key,
    )
    return await notification_broker.publish(event)


def encode_sse(event: NotificationEvent | None) -> bytes:
    if event is None:
        return b": heartbeat\n\n"

    payload = event.model_dump(mode="json")
    return (
        f"id: {event.event_id}\n"
        f"event: notification\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    ).encode("utf-8")


async def heartbeat_stream(
    events: AsyncIterator[NotificationEvent | None],
    *,
    heartbeat_seconds: float = 20.0,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[NotificationEvent | None] = asyncio.Queue()

    async def pump_events() -> None:
        async for event in events:
            await queue.put(event)

    task = asyncio.create_task(pump_events())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield encode_sse(None)
                continue
            yield encode_sse(event)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
