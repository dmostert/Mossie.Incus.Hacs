from __future__ import annotations

import os

from .client import ProtocolIncus
from .errors import IncusError
from .simplestreams import ProtocolSimpleStreams
from .transport import ConnectionArgs, Transport


def _init(client: ProtocolIncus) -> ProtocolIncus:
    if not client._s.args.skip_get_server:
        client.get_server()
    return client


def connect_incus(url: str, args: ConnectionArgs | None = None) -> ProtocolIncus:
    """Go: ConnectIncus (HTTPS)."""
    if not url.lower().startswith("https://"):
        raise IncusError("Only https URLs are supported for Incus")
    args = args or ConnectionArgs()
    return _init(ProtocolIncus(Transport(url.rstrip("/"), None, args), args))


def connect_incus_unix(path: str | None = None, args: ConnectionArgs | None = None) -> ProtocolIncus:
    """Go: ConnectIncusUnix. Empty path = $INCUS_SOCKET, else $INCUS_DIR/unix.socket, else /var/lib/incus/unix.socket."""
    if not path:
        path = os.environ.get("INCUS_SOCKET") or os.path.join(os.environ.get("INCUS_DIR") or "/var/lib/incus", "unix.socket")
    args = args or ConnectionArgs()
    return _init(ProtocolIncus(Transport("http://unix.socket", path, args), args))


def connect_public_incus(url: str, args: ConnectionArgs | None = None) -> ProtocolIncus:
    """Go: ConnectPublicIncus (read-only image access; same client class)."""
    return connect_incus(url, args)


def connect_simple_streams(url: str, args: ConnectionArgs | None = None) -> ProtocolSimpleStreams:
    """Go: ConnectSimpleStreams."""
    if not url.lower().startswith("https://"):
        raise IncusError("Only https URLs are supported for SimpleStreams")
    args = args or ConnectionArgs()
    return ProtocolSimpleStreams(Transport(url.rstrip("/"), None, args, default_port=443), args)
