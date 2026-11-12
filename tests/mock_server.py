"""Tiny Incus look-alike for tests: a threaded HTTP server on a Unix socket (or TLS TCP) that records requests."""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import ssl
import tempfile
import threading
from urllib.parse import urlsplit


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.headers.get("Transfer-Encoding") == "chunked":
            body = b""
            while True:
                size = int(self.rfile.readline().strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    break
                body += self.rfile.read(size)
                self.rfile.readline()
        parts = urlsplit(self.path)
        srv = self.server
        srv.requests.append({"method": self.command, "path": self.path, "route": parts.path, "query": parts.query,
                             "headers": dict(self.headers), "body": body})
        route = srv.routes.get((self.command, parts.path)) or srv.routes.get(("*", parts.path))
        if route is None:
            status, headers, payload = 404, {}, {"type": "error", "error": "not found", "error_code": 404}
        else:
            status, headers, payload = route(srv.requests[-1])
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        if not isinstance(payload, bytes):
            self.send_header("Content-Type", "application/json")
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _handle


def sync(metadata=None, status=200, headers=None):
    return status, headers or {}, {"type": "sync", "status": "Success", "status_code": 200, "metadata": metadata}


def error(message, code=400):
    return code, {}, {"type": "error", "error": message, "error_code": code}


def async_op(op_id="op1", metadata=None):
    return 202, {}, {"type": "async", "status": "Operation created", "status_code": 100, "operation": f"/1.0/operations/{op_id}",
                     "metadata": {"id": op_id, "class": "task", "status": "Running", "status_code": 103, "metadata": metadata}}


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path):
        super().__init__(path, _Handler)
        self.routes, self.requests = {}, []


class TlsServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, cert_pem: str, key_pem: str):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes, self.requests = {}, []
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        with tempfile.TemporaryDirectory() as d:
            c, k = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
            with open(c, "w") as f:
                f.write(cert_pem)
            with open(k, "w") as f:
                f.write(key_pem)
            ctx.load_cert_chain(c, k)
        self.socket = ctx.wrap_socket(self.socket, server_side=True)

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.server_address[1]}"


def serve(server):
    t = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    t.start()
    return t
