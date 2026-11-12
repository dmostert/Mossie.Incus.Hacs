from __future__ import annotations

import math
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from . import models
from .errors import IncusError
from .events import EventListener, EventTarget
from .helpers import StatusCode

if TYPE_CHECKING:
    from .client import ProtocolIncus


class Operation:
    """Go: Operation. A server-side background operation."""

    def __init__(self, client: "ProtocolIncus", info: models.Operation):
        self._client = client
        self._info = info
        # Event handling (Go: operation.setupListener / AddHandler): one events websocket is shared by all handlers.
        self._lock = threading.Lock()          # guards _handlers, _listener and the _info transitions seen by events
        self._setup = threading.Lock()         # serialises creating the listener
        self._handlers: dict[EventTarget, Callable[[models.Operation], Any]] = {}
        self._listener: EventListener | None = None

    @property
    def _final(self) -> bool:
        return StatusCode.is_final(self._info.status_code)

    def get(self) -> models.Operation:
        return self._info

    def wait(self, timeout: float | None = None) -> None:
        """Blocks until the operation finishes; raises IncusError if it failed, TimeoutError on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._final:
            if deadline is None:
                secs = -1
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for the operation")
                secs = max(1, math.ceil(remaining))
            self._info, _ = self._client.get_operation_wait(self._info.id, secs)
            if deadline is not None and not self._final and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the operation")
        if self._info.err:
            raise IncusError(self._info.err)

    def refresh(self) -> None:
        self._info, _ = self._client.get_operation(self._info.id)

    def cancel(self) -> None:
        self._client.delete_operation(self._info.id)

    def get_websocket(self, secret: str) -> Any:
        return self._client.get_operation_websocket(self._info.id, secret)

    def add_handler(self, handler: Callable[[models.Operation], Any]) -> EventTarget | None:
        """Calls ``handler(operation)`` for every update received from now on (from the event-stream thread, in order).

        Returns None, registering nothing, if the operation has already finished: read the outcome with ``get()`` / ``wait()``.
        The shared events connection is closed automatically when the operation finishes or the last handler is removed."""
        if self._final:
            return None
        self._ensure_listener()                 # also re-syncs the state: closes the race with fast operations
        target = EventTarget(lambda e: None, ["operation"])    # token handed back to the caller
        with self._lock:
            # Atomic with _on_event's state update: either the final event was processed first (-> None) or this handler
            # is registered before it and receives it.
            if self._final:
                return None
            self._handlers[target] = handler
        return target

    def remove_handler(self, target: EventTarget) -> None:
        with self._lock:
            found = self._handlers.pop(target, None) is not None
            empty = not self._handlers
        if not found:
            if self._final:
                return                           # handlers are dropped when the operation ends: removing is a no-op
            raise IncusError("Couldn't find this handler")
        if empty:
            self._close_listener()

    def _ensure_listener(self) -> None:
        with self._setup:
            if self._listener is not None:
                return
            listener = self._client.get_events_all_projects()
            listener.add_handler(["operation"], self._on_event)
            with self._lock:
                self._listener = listener
            try:
                # The operation may have progressed (or finished) before the events connection was up: events sent in
                # that window are lost, so read the current state once.
                self.refresh()
            except BaseException:
                self._close_listener()
                raise
            if self._final:
                self._close_listener()

    def _on_event(self, e: models.Event) -> None:
        if not isinstance(e.metadata, dict):
            return
        op = models.Operation.from_dict(e.metadata)
        if op.id != self._info.id:
            return
        with self._lock:
            self._info = op
            handlers = list(self._handlers.values())
        for h in handlers:
            try:
                h(op)
            except Exception:  # noqa: BLE001 - one failing handler must not affect the others
                pass
        if StatusCode.is_final(op.status_code):   # nothing more will ever be sent for this operation
            self._close_listener()

    def _close_listener(self) -> None:
        with self._lock:
            listener, self._listener = self._listener, None
            self._handlers.clear()
        if listener is not None:
            listener.disconnect()


class RemoteOperation:
    """Go: RemoteOperation. Waits for the target operation (and any helper operations, e.g. the source side of a migration)."""

    def __init__(self, target: Operation, *also: Operation | None):
        self._target = target
        self._also = [o for o in also if o is not None]

    def add_handler(self, handler: Callable[[models.Operation], Any]) -> EventTarget | None:
        return self._target.add_handler(handler)

    def cancel_target(self) -> None:
        self._target.cancel()

    def get_target(self) -> models.Operation:
        return self._target.get()

    def wait(self, timeout: float | None = None) -> None:
        self._target.wait(timeout)
        for o in self._also:
            o.wait(timeout)
