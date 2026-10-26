"""Pub/Sub in-memory event broadcaster for chapter lifecycle and SSE streaming per Spec 07."""

import asyncio
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple


class ChapterEventBus:
    """Manages SSE subscribers and broadcasts chapter-scoped events with revision tracking."""

    def __init__(self):
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._history: Dict[str, List[dict]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def subscribe(
        self, chapter_uuid: str, last_event_id: Optional[int] = None
    ) -> Tuple[asyncio.Queue, bool]:
        """
        Register a new SSE client queue for a chapter.
        Returns: (queue, snapshot_required: bool)
        If last_event_id is provided and is older than the oldest retained history, returns snapshot_required=True.
        """
        queue: asyncio.Queue = asyncio.Queue()
        snapshot_required = False

        async with self._lock:
            self._subscribers[chapter_uuid].add(queue)
            history = self._history[chapter_uuid]

            if last_event_id is not None and history:
                oldest_id = history[0].get("id")
                if oldest_id is not None and last_event_id < oldest_id:
                    snapshot_required = True
                else:
                    for event in history:
                        ev_id = event.get("id")
                        if ev_id is not None and ev_id > last_event_id:
                            await queue.put(event)
            elif last_event_id is None:
                # Fresh connection without Last-Event-ID: replay all available history
                for event in history:
                    await queue.put(event)

        return queue, snapshot_required

    async def unsubscribe(self, chapter_uuid: str, queue: asyncio.Queue) -> None:
        """Unregister a client queue."""
        async with self._lock:
            if chapter_uuid in self._subscribers:
                self._subscribers[chapter_uuid].discard(queue)
                if not self._subscribers[chapter_uuid]:
                    del self._subscribers[chapter_uuid]

    async def emit(
        self,
        chapter_uuid: str,
        event_type: str,
        payload: dict,
        run_generation: Optional[int] = None,
        state_revision: Optional[int] = None,
    ) -> None:
        """Broadcast an event with envelope to subscribers for the chapter."""
        # Ensure envelope fields
        if run_generation is not None and "run_generation" not in payload:
            payload["run_generation"] = run_generation
        if state_revision is not None and "state_revision" not in payload:
            payload["state_revision"] = state_revision
        if "chapter_uuid" not in payload and chapter_uuid:
            payload["chapter_uuid"] = chapter_uuid

        event_id = payload.get("state_revision") or state_revision
        event = {
            "id": event_id,
            "event": event_type,
            "data": payload,
        }

        async with self._lock:
            # Store in replay history (cap at last 150 events)
            if chapter_uuid:
                self._history[chapter_uuid].append(event)
                if len(self._history[chapter_uuid]) > 150:
                    self._history[chapter_uuid].pop(0)

                subscribers = list(self._subscribers.get(chapter_uuid, set()))
            else:
                # Global broadcast to all chapters if chapter_uuid empty
                subscribers = [q for q_set in self._subscribers.values() for q in q_set]

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


