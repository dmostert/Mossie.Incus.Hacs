"""Go: shared/ws (the parts that matter for client use)."""
from __future__ import annotations

import io
from typing import Any


class WebSocketStream(io.RawIOBase):
    """Exposes a websocket (any object with recv()/send()/close()) as a duplex binary file-like object."""

    def __init__(self, ws: Any):
        self._ws = ws
        self._buf = b""
        self._eof = False

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if not self._buf and not self._eof:
            try:
                msg = self._ws.recv()
            except Exception as e:  # noqa: BLE001
                if type(e).__name__.startswith("ConnectionClosed"):
                    self._eof = True
                    return 0
                raise
            self._buf = msg if isinstance(msg, bytes) else msg.encode()
        n = min(len(b), len(self._buf))
        b[:n], self._buf = self._buf[:n], self._buf[n:]
        return n

    def write(self, data) -> int:
        self._ws.send(bytes(data))
        return len(data)

    def close(self) -> None:
        try:
            self._ws.close()
        finally:
            super().close()
