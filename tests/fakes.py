import queue


class ConnectionClosedOK(Exception):
    """Same name as websockets' exception so incus_client.events.is_closed() recognises it."""


_CLOSE = object()


class FakeWS:
    """Minimal websocket stand-in: recv() pops queued messages, then raises ConnectionClosedOK once closed."""

    def __init__(self, messages=(), close_when_drained=True):
        self.q = queue.Queue()
        for m in messages:
            self.q.put(m)
        if close_when_drained:
            self.q.put(_CLOSE)
        self.sent, self.closed = [], False

    def recv(self):
        item = self.q.get(timeout=5)
        if item is _CLOSE:
            self.q.put(_CLOSE)
            raise ConnectionClosedOK()
        return item

    def send(self, msg):
        self.sent.append(msg)

    def close(self):
        self.closed = True
        self.q.put(_CLOSE)
