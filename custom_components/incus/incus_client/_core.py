"""Core of ProtocolIncus: raw requests, URL/query helpers, operations, events, websocket pumps."""
from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote, unquote

from . import models
from ._base import ApiObject, convert
from .errors import IncusError
from .events import EventListener, is_closed
from .operations import Operation
from .transport import ConnectionArgs, HTTPStream, Transport


def E(s: str) -> str:
    """URL-escape one path segment or query value (Go: url.PathEscape / QueryEscape)."""
    return quote(s, safe="")


def Q(*pairs: tuple[str, str | None]) -> str | None:
    """Builds a query string from (key, value) pairs, skipping empty values."""
    s = "&".join(f"{k}={quote(v, safe='')}" for k, v in pairs if v)
    return s or None


def F(filters) -> str | None:
    """Go: parseFilters. "key=value" becomes "key eq value"; items are joined with " and "."""
    if filters is None:
        return None
    parts = [f"{f.split('=', 1)[0]} eq {f.split('=', 1)[1]}" if "=" in f else f for f in filters]
    return " and ".join(parts) or None


@dataclass
class ApiResponse:
    """Go: api.Response, the standard envelope around every API reply."""
    type: str = ""
    status: str = ""
    status_code: int = 0
    operation: str = ""
    error_code: int = 0
    error: str = ""
    metadata: Any = None

    def get_metadata(self, hint: Any = None) -> Any:
        return self.metadata if hint is None else convert(self.metadata, hint)


@dataclass
class ConnectionInfo:
    """Go: ConnectionInfo."""
    addresses: list[str] = field(default_factory=list)
    certificate: str = ""
    protocol: str = ""
    url: str = ""
    socket_path: str = ""
    project: str = ""
    target: str = ""


class _Shared:
    def __init__(self, transport: Transport, args: ConnectionArgs):
        self.transport = transport
        self.args = args
        self.server: models.Server | None = None
        self.require_authenticated = False


class Core:
    def __init__(self, transport: Transport, args: ConnectionArgs | None = None):
        self._s = _Shared(transport, args or transport.args)
        self._project = ""
        self._target = ""

    # ---- connection-level ------------------------------------------------------------------
    def disconnect(self) -> None:
        """Connections are not kept open between requests, so there is nothing to release."""

    def get_connection_info(self) -> ConnectionInfo:
        t, env = self._s.transport, (self._s.server.environment if self._s.server else None)
        addrs = [] if t.is_unix else [f"{t.host}:{t.port}"]
        addrs += [a for a in (env.addresses or []) if a not in addrs] if env else []
        return ConnectionInfo(
            addresses=addrs,
            certificate=self._s.args.tls_server_cert or ((env.certificate or "") if env else ""),
            protocol="incus", url=t.base_url, socket_path=t.unix_path or "", project=self._project, target=self._target)

    def use_project(self, name: str):
        """Go: UseProject. Returns a view of this connection scoped to a project."""
        c = copy.copy(self)
        c._project = name
        return c

    def use_target(self, name: str):
        """Go: UseTarget. Returns a view of this connection targeting one cluster member."""
        c = copy.copy(self)
        c._target = name
        return c

    def has_extension(self, extension: str) -> bool:
        s = self._s.server
        return True if s is None or s.api_extensions is None else extension in s.api_extensions

    def check_extension(self, extension_name: str) -> None:
        if not self.has_extension(extension_name):
            raise IncusError(f'The server is missing the required "{extension_name}" API extension')

    def is_clustered(self) -> bool:
        s = self._s.server
        return bool(s and s.environment and s.environment.server_clustered)

    def is_agent(self) -> bool:
        s = self._s.server
        return bool(s and s.environment and s.environment.server == "incus-agent")

    def require_authenticated(self, authenticated: bool) -> None:
        self._s.require_authenticated = authenticated

    def get_server(self) -> tuple[models.Server, str]:
        srv, etag = self._get_e(models.Server, "")
        self._s.server = srv
        return srv, etag

    def update_server(self, server: models.ServerPut, etag: str) -> None:
        self._send("PUT", "", server, etag)

    def get_metadata_configuration(self) -> models.MetadataConfiguration:
        return self._get(models.MetadataConfiguration, "/metadata/configuration")

    def get_server_resources(self) -> models.Resources:
        return self._get(models.Resources, "/resources")

    def get_metrics(self) -> str:
        with self._raw_get(self._u("/metrics")) as r:
            return r.read().decode()

    def do_http(self, method: str, path: str, body=None, headers: dict | None = None):
        """Go: DoHTTP. Low-level request; returns (status, headers, body_bytes)."""
        status, _, hdrs, data = self._s.transport.request(method, path, body, headers)
        return status, hdrs, data

    # ---- raw access ------------------------------------------------------------------------
    def raw_query(self, method: str, path: str, data: Any = None, etag: str = "",
                  headers: dict | None = None) -> tuple[ApiResponse, str]:
        """Go: RawQuery. ``path`` is the full API path (e.g. "/1.0/instances").
        ``data`` may be a model, dict/list (sent as JSON), bytes or a binary file-like object (sent as octet-stream)."""
        s = self._s
        if s.require_authenticated and s.server is not None and s.server.auth != "trusted":
            raise IncusError("The server isn't authenticated", 403)
        body, hdrs = _prepare_body(data, dict(headers or {}))
        if etag:
            hdrs["If-Match"] = etag
        status, reason, rheaders, raw = s.transport.request(method, path, body, hdrs)
        return _parse(status, reason, rheaders, raw, path)

    def raw_operation(self, method: str, path: str, data: Any = None, etag: str = "",
                      headers: dict | None = None) -> tuple[Operation, str]:
        """Go: RawOperation. Expects an asynchronous (operation) reply."""
        resp, e = self.raw_query(method, path, data, etag, headers)
        if resp.type != "async":
            raise IncusError("Server didn't return an operation")
        info = resp.get_metadata(models.Operation)
        if info is None:
            raise IncusError("Server returned an operation without metadata")
        return Operation(self, info), e  # type: ignore[arg-type]

    def raw_websocket(self, path: str):
        """Go: RawWebsocket. ``path`` is the full path incl. query, e.g. "/1.0/events". Needs the ``websockets`` package."""
        try:
            from websockets.sync.client import connect
        except ImportError as e:  # pragma: no cover
            raise IncusError("Websocket support needs the 'websockets' package (pip install websockets)") from e
        t = self._s.transport
        headers = {"User-Agent": self._s.args.user_agent or "incus-python-client"}
        if not t.is_unix and self._s.args.bearer_token:
            headers["Authorization"] = "Bearer " + self._s.args.bearer_token
        sock = t.connect_socket()  # already TLS-wrapped for HTTPS, so the websocket URL uses ws://
        host = "localhost" if t.is_unix else f"{t.host}:{t.port}"
        try:
            return connect(f"ws://{host}{path}", sock=sock, additional_headers=headers, max_size=None, compression=None)
        except BaseException:
            sock.close()
            raise

    # ---- helpers used by every endpoint group -----------------------------------------------
    def _u(self, rel: str, query: str | None = None, all_projects: bool = False) -> str:
        """Go: setQueryAttributes. Builds /1.0{rel}?{query}&project=..&target=.. (project/target only when set)."""
        q = [query] if query else []
        if all_projects:
            q.append("all-projects=true")
        elif self._project:
            q.append("project=" + E(self._project))
        if self._target:
            q.append("target=" + E(self._target))
        return "/1.0" + rel + ("?" + "&".join(q) if q else "")

    def _get(self, hint: Any, rel: str, q: str | None = None, all_projects: bool = False) -> Any:
        r, _ = self.raw_query("GET", self._u(rel, q, all_projects))
        return r.get_metadata(hint)

    def _get_e(self, hint: Any, rel: str, q: str | None = None) -> tuple[Any, str]:
        r, e = self.raw_query("GET", self._u(rel, q))
        return r.get_metadata(hint), e

    def _get_list(self, cls: Any, rel: str, q: str | None = None, all_projects: bool = False) -> list:
        return self._get(list[cls], rel, q, all_projects) or []

    def _send(self, method: str, rel: str, body: Any = None, etag: str = "", q: str | None = None,
              headers: dict | None = None) -> ApiResponse:
        return self.raw_query(method, self._u(rel, q), body, etag, headers)[0]

    def _send_op(self, method: str, rel: str, body: Any = None, etag: str = "", q: str | None = None,
                 headers: dict | None = None) -> Operation:
        return self.raw_operation(method, self._u(rel, q), body, etag, headers)[0]

    def _names(self, rel: str, q: str | None = None) -> list[str]:
        """Go: urlsToResourceNames."""
        return [_name_from_url(rel, u) for u in self._get(list[str], rel, q) or []]

    def _names_all_projects(self, rel: str, q: str | None = None) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for u in self._get(list[str], rel, q, True) or []:
            project = "default"
            if "?" in u:
                for kv in u.split("?", 1)[1].split("&"):
                    if kv.startswith("project="):
                        project = unquote(kv[8:])
            result.setdefault(project, []).append(_name_from_url(rel, u))
        return result

    def _raw_get(self, path: str, headers: dict | None = None) -> HTTPStream:
        """GET a non-JSON resource (file, log, export). Raises IncusError on failure."""
        stream = self._s.transport.request_stream("GET", path, None, headers)
        if stream.status >= 400:
            try:
                data = stream.read()
            finally:
                stream.close()
            _parse(stream.status, "", stream.headers, data, path)
            raise IncusError(f"Failed to fetch {path}: {stream.status}", stream.status)
        return stream

    def _raw_stream(self, rel: str, q: str | None = None) -> HTTPStream:
        return self._raw_get(self._u(rel, q))

    def _same_server(self, info: ConnectionInfo) -> bool:
        t = self._s.transport
        if info.protocol != "incus":
            return False
        return info.socket_path == t.unix_path if t.is_unix else (info.socket_path == "" and info.url == t.base_url)

    # ---- operations --------------------------------------------------------------------------
    def get_operation_uuids(self) -> list[str]:
        d = self._get(dict[str, list[str]], "/operations") or {}
        return [_name_from_url("/operations", u) for v in d.values() for u in v]

    def get_operations(self) -> list[models.Operation]:
        d = self._get(dict[str, list[models.Operation]], "/operations", Q(("recursion", "1"))) or {}
        return [o for v in d.values() for o in v]

    def get_operations_all_projects(self) -> list[models.Operation]:
        d = self._get(dict[str, list[models.Operation]], "/operations", Q(("recursion", "1")), True) or {}
        return [o for v in d.values() for o in v]

    def get_operation(self, uuid: str) -> tuple[models.Operation, str]:
        return self._get_e(models.Operation, f"/operations/{E(uuid)}")

    def get_operation_wait(self, uuid: str, timeout: int) -> tuple[models.Operation, str]:
        return self._get_e(models.Operation, f"/operations/{E(uuid)}/wait", Q(("timeout", str(timeout))))

    def get_operation_wait_secret(self, uuid: str, secret: str, timeout: int) -> tuple[models.Operation, str]:
        return self._get_e(models.Operation, f"/operations/{E(uuid)}/wait", Q(("timeout", str(timeout)), ("secret", secret)))

    def get_operation_websocket(self, uuid: str, secret: str):
        return self.raw_websocket(f"/1.0/operations/{E(uuid)}/websocket" + (f"?secret={E(secret)}" if secret else ""))

    def delete_operation(self, uuid: str) -> None:
        self._send("DELETE", f"/operations/{E(uuid)}")

    # ---- events ------------------------------------------------------------------------------
    def get_events(self) -> EventListener:
        return self._open_events(False)

    def get_events_all_projects(self) -> EventListener:
        return self._open_events(True)

    def _open_events(self, all_projects: bool) -> EventListener:
        q = "?all-projects=true" if all_projects else (f"?project={E(self._project)}" if self._project else "")
        return EventListener(self.raw_websocket("/1.0/events" + q))

    def send_event(self, event: models.Event) -> None:
        self.check_extension("event_hub")
        ws = self.raw_websocket("/1.0/events")
        try:
            ws.send(json.dumps(event.to_dict()))
        finally:
            ws.close()


def _name_from_url(rel: str, url: str) -> str:
    path = url.split("?", 1)[0]
    prefix = "/1.0" + rel + "/"
    if path.startswith(prefix):
        rest = path[len(prefix):]
    else:
        seg = "/" + rel.rsplit("/", 1)[-1] + "/"
        i = path.rfind(seg)
        rest = path[i + len(seg):] if i >= 0 else path.rsplit("/", 1)[-1]
    return unquote(rest)


def _prepare_body(data: Any, headers: dict) -> tuple[Any, dict]:
    if data is None:
        return None, headers
    if isinstance(data, ApiObject):
        data = data.to_dict()
    if isinstance(data, (dict, list)):
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(data).encode(), headers
    if isinstance(data, str):
        data = data.encode()
    headers.setdefault("Content-Type", "application/octet-stream")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data), headers
    if hasattr(data, "read"):  # binary file-like: send with Content-Length when we can work it out
        try:
            headers.setdefault("Content-Length", str(os.fstat(data.fileno()).st_size - data.tell()))
        except (AttributeError, OSError, ValueError):
            pass
        return data, headers
    raise TypeError(f"Unsupported request body type: {type(data).__name__}")


def _parse(status: int, reason: str, headers: Any, raw: bytes, path: str) -> tuple[ApiResponse, str]:
    etag = headers.get("ETag", "") if headers is not None else ""
    try:
        d = json.loads(raw) if raw else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not d.get("type"):
        if status >= 400:
            raise IncusError(f"Failed to fetch {path}: {status} {reason}".strip(), status)
        raise IncusError(f"Failed to parse the response for {path}", status)
    r = ApiResponse(type=d.get("type", ""), status=d.get("status", ""), status_code=d.get("status_code", 0),
                    operation=d.get("operation", ""), error_code=d.get("error_code", 0), error=d.get("error", ""),
                    metadata=d.get("metadata"))
    if r.type == "error":
        raise IncusError(r.error, r.error_code or status)
    if status >= 400:
        raise IncusError(f"Failed to fetch {path}: {status} {reason}".strip(), status)
    return r, etag


# ---- websocket data pumps shared by exec/console --------------------------------------------
def pump(ws: Any, stdin: Any, stdout: Any, *, close_on_eof: bool = True) -> threading.Thread:
    """websocket -> stdout until the peer closes (returned thread); stdin -> websocket until EOF (background thread)."""
    if stdin is not None:
        def writer() -> None:
            try:
                read = getattr(stdin, "read1", stdin.read)
                while chunk := read(16384):
                    ws.send(chunk)
                if close_on_eof:
                    ws.close()
            except Exception:
                pass
        threading.Thread(target=writer, daemon=True).start()

    def reader() -> None:
        try:
            while True:
                msg = ws.recv()
                if stdout is not None and msg:
                    stdout.write(msg if isinstance(msg, bytes) else msg.encode())
                    getattr(stdout, "flush", lambda: None)()
        except Exception as e:  # noqa: BLE001
            if not is_closed(e):
                raise
        finally:
            try:
                ws.close()
            except Exception:
                pass

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    return th
