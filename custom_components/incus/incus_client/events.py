from __future__ import annotations

import json
import threading
from typing import Any, Callable, Iterable

from . import models
from .errors import IncusError


def is_closed(exc: BaseException) -> bool:
    """True for websockets' ConnectionClosed* exceptions (matched by name so the package stays optional)."""
    return type(exc).__name__.startswith("ConnectionClosed") or isinstance(exc, (EOFError, ConnectionError, OSError))


class EventTarget:
    """Go: EventTarget. Returned by add_handler and used with remove_handler."""

    def __init__(self, function: Callable[[models.Event], Any], types: Iterable[str] | None):
        self.function = function
        self.types = set(types) if types else None


class EventListener:
    """Go: EventListener. Wraps an events websocket (any object with recv()/close()) and dispatches events from a thread."""

    def __init__(self, ws: Any):
        self._ws = ws
        self._lock = threading.Lock()
        self._targets: list[EventTarget] = []
        self._done = threading.Event()
        self._error: BaseException | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._loop, name="incus-events", daemon=True)
        self._thread.start()

    @property
    def is_active(self) -> bool:
        return not self._done.is_set()

    def add_handler(self, types: Iterable[str] | None, function: Callable[[models.Event], Any]) -> EventTarget:
        """Calls ``function(event)`` for every received event of the given types (None/empty = all)."""
        if not self.is_active:
            raise IncusError("The event listener is no longer active")
        target = EventTarget(function, types)
        with self._lock:
            self._targets.append(target)
        return target

    def remove_handler(self, target: EventTarget) -> None:
        if not self.is_active:
            raise IncusError("The event listener is no longer active")
        with self._lock:
            try:
                self._targets.remove(target)
            except ValueError:
                raise IncusError("Couldn't find this function and event types combination") from None

    def disconnect(self) -> None:
        """Must be called once done listening for events."""
        self._closing = True
        try:
            self._ws.close()
        except Exception:
            pass

    def wait(self, timeout: float | None = None) -> None:
        """Blocks until the stream ends; raises if it failed."""
        if not self._done.wait(timeout):
            raise TimeoutError("Timed out waiting for the event listener to finish")
        if self._error is not None:
            raise IncusError(f"Event stream failed: {self._error}")

    def _loop(self) -> None:
        try:
            while True:
                msg = self._ws.recv()
                if isinstance(msg, bytes):
                    msg = msg.decode()
                try:
                    event = models.Event.from_dict(json.loads(msg))
                except ValueError:
                    continue
                with self._lock:
                    targets = list(self._targets)
                for t in targets:
                    if t.types is not None and (event.type is None or event.type not in t.types):
                        continue
                    try:
                        t.function(event)
                    except Exception:
                        pass  # handler errors must not kill the stream
        except Exception as e:  # noqa: BLE001
            if not (self._closing or is_closed(e)):
                self._error = e
        finally:
            self._done.set()
            try:
                self._ws.close()
            except Exception:
                pass
