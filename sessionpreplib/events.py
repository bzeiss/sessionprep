from __future__ import annotations

from typing import Any, Callable


class EventBus:
    """Lightweight publish/subscribe bus for pipeline progress events."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = {}

    def subscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Register a handler for an event type."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Remove a handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event_type: str, **data: Any) -> None:
        """Fire all handlers for an event type."""
        for handler in self._handlers.get(event_type, []):
            handler(**data)
