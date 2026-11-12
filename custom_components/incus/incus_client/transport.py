"""HTTP/WebSocket plumbing: Unix socket or HTTPS (client certs, pinned server cert, custom CA). Standard library only."""
from __future__ import annotations

import http.client
import logging
import os
import socket
import ssl
import tempfile
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import IncusError

_LOG = logging.getLogger(__name__)


@dataclass
class ConnectionArgs:
    """Go: ConnectionArgs."""
    tls_server_cert: str = ""      # PEM; the server certificate is pinned (exact match)
    tls_client_cert: str = ""      # PEM
    tls_client_key: str = ""       # PEM
    tls_ca: str = ""               # PEM CA bundle (PKI mode)
    user_agent: str = ""
    auth_type: str = ""            # "tls" (default) or "oidc"
    insecure_skip_verify: bool = False
    bearer_token: str = ""         # Go: OIDCTokens - sent as "Authorization: Bearer"
    skip_get_server: bool = False
    timeout: float | None = None   # socket timeout in seconds (None = block)
    cache_path: str = ""           # accepted for parity; not implemented
    cache_expiry: float = 0.0


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float | None):
        super().__init__("localhost", timeout=timeout)
        self._unix_path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(self.timeout)
            s.connect(self._unix_path)
        except BaseException:
            s.close()
            raise
        self.sock = s


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ctx: ssl.SSLContext, pinned_der: bytes | None, timeout: float | None):
        super().__init__(host, port, context=ctx, timeout=timeout)
        self._pinned = pinned_der

    def connect(self) -> None:
        super().connect()
        _check_pinned(self.sock, self._pinned)


def _check_pinned(sock: ssl.SSLSocket, pinned_der: bytes | None) -> None:
    if pinned_der is None:
        return
    if sock.getpeercert(binary_form=True) != pinned_der:
        sock.close()
        raise IncusError("The server certificate doesn't match the pinned certificate")


class HTTPStream:
    """Streaming response body (file, log, export). Closing it releases the connection."""

    def __init__(self, resp: http.client.HTTPResponse, conn: http.client.HTTPConnection):
        self._resp, self._conn = resp, conn
        self.status, self.headers = resp.status, resp.headers

    def read(self, n: int = -1) -> bytes:
        return self._resp.read() if n < 0 else self._resp.read(n)

    def iter_chunks(self, size: int = 65536):
        while chunk := self._resp.read(size):
            yield chunk

    def close(self) -> None:
        try:
            self._resp.close()
        finally:
            self._conn.close()

    def __enter__(self) -> "HTTPStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Transport:
    def __init__(self, base_url: str, unix_path: str | None, args: ConnectionArgs, default_port: int = 8443):
        self.base_url = base_url
        self.unix_path = unix_path
        self.args = args
        self.is_unix = unix_path is not None
        parts = urlsplit(base_url)
        self.host = parts.hostname or "localhost"
        self.port = parts.port or (default_port if parts.scheme == "https" else 80)
        self.path_prefix = parts.path.rstrip("/")
        self._pinned_der = ssl.PEM_cert_to_DER_cert(args.tls_server_cert) if args.tls_server_cert.strip() else None
        self._ctx = None if self.is_unix else self._build_context()

    # -- TLS ---------------------------------------------------------------------------------
    def _build_context(self) -> ssl.SSLContext:
        a = self.args
        if a.insecure_skip_verify or self._pinned_der is not None:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif a.tls_ca.strip():
            ctx = ssl.create_default_context(cadata=a.tls_ca)
        else:
            ctx = ssl.create_default_context()
        if a.tls_client_cert.strip() and a.tls_client_key.strip():
            with tempfile.TemporaryDirectory() as d:  # load_cert_chain needs files
                cp, kp = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
                for p, data in ((cp, a.tls_client_cert), (kp, a.tls_client_key)):
                    fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
                    with os.fdopen(fd, "w") as f:
                        f.write(data)
                ctx.load_cert_chain(cp, kp)
        return ctx

    # -- connections -------------------------------------------------------------------------
    def open(self) -> http.client.HTTPConnection:
        if self.is_unix:
            return _UnixHTTPConnection(self.unix_path, self.args.timeout)  # type: ignore[arg-type]
        return _PinnedHTTPSConnection(self.host, self.port, self._ctx, self._pinned_der, self.args.timeout)  # type: ignore[arg-type]

    def connect_socket(self) -> socket.socket:
        """A connected (and, for HTTPS, TLS-wrapped + pinned-verified) raw socket: used for websockets and HTTP upgrades."""
        if self.is_unix:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.settimeout(self.args.timeout)
                s.connect(self.unix_path)  # type: ignore[arg-type]
            except BaseException:
                s.close()
                raise
            return s
        raw = socket.create_connection((self.host, self.port), timeout=self.args.timeout)
        try:
            tls = self._ctx.wrap_socket(raw, server_hostname=self.host)  # type: ignore[union-attr]
        except BaseException:
            raw.close()
            raise
        _check_pinned(tls, self._pinned_der)
        return tls

    def _headers(self, headers: dict | None) -> dict:
        h = {"User-Agent": self.args.user_agent or "incus-python-client"}
        if not self.is_unix and self.args.bearer_token:
            h["Authorization"] = "Bearer " + self.args.bearer_token
        h.update(headers or {})
        return h

    # -- requests ----------------------------------------------------------------------------
    def request(self, method: str, path: str, body=None, headers: dict | None = None):
        """Returns (status, reason, headers, body_bytes)."""
        conn = self.open()
        try:
            conn.request(method, path, body=body, headers=self._headers(headers))
            resp = conn.getresponse()
            _LOG.debug("%s %s -> %s", method, path, resp.status)
            return resp.status, resp.reason, resp.headers, resp.read()
        except BaseException as e:
            _LOG.debug("%s %s on %s failed: %r", method, path, self.unix_path or f"{self.host}:{self.port}", e)
            raise
        finally:
            conn.close()

    def request_stream(self, method: str, path: str, body=None, headers: dict | None = None) -> HTTPStream:
        conn = self.open()
        try:
            conn.request(method, path, body=body, headers=self._headers(headers))
            return HTTPStream(conn.getresponse(), conn)
        except BaseException:
            conn.close()
            raise

    def upgrade(self, path: str, protocol: str) -> socket.socket:
        """HTTP/1.1 Upgrade (used by the SFTP endpoint). Returns the raw duplex socket after the 101 response."""
        s = self.connect_socket()
        hdrs = self._headers({"Host": self.host, "Connection": "Upgrade", "Upgrade": protocol})
        s.sendall((f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                s.close()
                raise IncusError("Connection closed during protocol upgrade")
            buf += chunk
        status_line = buf.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101" not in status_line:
            s.close()
            raise IncusError(f"Server did not switch protocols: {status_line}")
        return s
