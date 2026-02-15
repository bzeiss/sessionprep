from __future__ import annotations

import threading
from typing import Any, Callable


class EventBus:
    """Lightweight publish/subscribe bus for pipeline progress events.

    Thread-safe: all operations are protected by a lock so the bus can be
    shared across worker threads in the parallel analysis pipeline.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Register a handler for an event type."""
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Remove a handler."""
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def emit(self, event_type: str, **data: Any) -> None:
        """Fire all handlers for an event type."""
        with self._lock:
            handlers = list(self._handlers.get(event_type, []))
        for handler in handlers:
            handler(**data)
