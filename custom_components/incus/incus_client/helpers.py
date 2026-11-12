"""Hand-written parts of Go's shared/api: status codes, constants, URL builder, event logging, model mixins."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .errors import IncusError


class StatusCode:
    """Go: api.StatusCode constants."""
    OPERATION_CREATED, STARTED, STOPPED, RUNNING, CANCELLING, PENDING, STARTING = 100, 101, 102, 103, 104, 105, 106
    STOPPING, ABORTING, FREEZING, FROZEN, THAWED, ERROR, READY = 107, 108, 109, 110, 111, 112, 113
    SUCCESS, FAILURE, CANCELLED = 200, 400, 401

    _NAMES = {100: "Operation created", 101: "Started", 102: "Stopped", 103: "Running", 104: "Cancelling", 105: "Pending",
              106: "Starting", 107: "Stopping", 108: "Aborting", 109: "Freezing", 110: "Frozen", 111: "Thawed",
              112: "Error", 113: "Ready", 200: "Success", 400: "Failure", 401: "Cancelled"}

    @classmethod
    def name(cls, code: int) -> str:
        return cls._NAMES.get(code, f"Unknown ({code})")

    @staticmethod
    def is_final(code: int) -> bool:
        """True for Success, Failure and Cancelled."""
        return code >= 200


class EventTypes:
    LOGGING, OPERATION, LIFECYCLE = "logging", "operation", "lifecycle"


class ResponseTypes:
    SYNC, ASYNC, ERROR = "sync", "async", "error"


class InstanceTypes:
    ANY, CONTAINER, VIRTUAL_MACHINE = "", "container", "virtual-machine"


class CertificateTypes:
    CLIENT, SERVER, METRICS = "client", "server", "metrics"


class InstanceActions:
    START, STOP, RESTART, FREEZE, UNFREEZE = "start", "stop", "restart", "freeze", "unfreeze"


class ApiUrl:
    """Go: api.URL. Builds API URLs with escaped path segments and the usual project/target query parameters."""

    def __init__(self) -> None:
        self._scheme = self._host = ""
        self._segments: list[str] = []
        self._query: list[tuple[str, str]] = []

    def scheme(self, scheme: str) -> "ApiUrl":
        self._scheme = scheme
        return self

    def host(self, host: str) -> "ApiUrl":
        self._host = host
        return self

    def path(self, *segments: str) -> "ApiUrl":
        self._segments += [quote(s, safe="") for s in segments]
        return self

    def with_query(self, key: str, value: str) -> "ApiUrl":
        self._query.append((key, value))
        return self

    def project(self, name: str) -> "ApiUrl":
        """Adds ?project= unless empty or "default"."""
        return self if name in ("", "default") else self.with_query("project", name)

    def target(self, cluster_member: str) -> "ApiUrl":
        return self if cluster_member == "" else self.with_query("target", cluster_member)

    def __str__(self) -> str:
        path = "/" + "/".join(self._segments) if self._segments else ""
        query = "?" + "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in self._query) if self._query else ""
        prefix = f"{self._scheme}://{self._host}" if self._scheme else self._host
        return prefix + path + query


@dataclass
class EventLogRecord:
    time: datetime | None = None
    level: str | None = None
    message: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


class EventMixin:
    def to_logging(self) -> EventLogRecord:
        """Go: Event.ToLogging (from memory - verify lifecycle format against shared/api/event.go)."""
        from . import models
        md = getattr(self, "metadata", None)
        if md is None:
            raise IncusError("Event has no metadata")
        if self.type == EventTypes.LOGGING:  # type: ignore[attr-defined]
            e = models.EventLogging.from_dict(md)
            return EventLogRecord(self.timestamp, e.level, e.message, dict(e.context or {}))  # type: ignore[attr-defined]
        if self.type == EventTypes.LIFECYCLE:  # type: ignore[attr-defined]
            e = models.EventLifecycle.from_dict(md)
            ctx = dict(e.context or {})
            if e.requestor is not None:
                ctx["requester-address"] = e.requestor.address
                ctx["requester-protocol"] = e.requestor.protocol
                ctx["requester-username"] = e.requestor.username
            return EventLogRecord(self.timestamp, "info", f"Action: {e.action}, Source: {e.source}", ctx)  # type: ignore[attr-defined]
        raise IncusError("Not a logging type event")


class InstanceMixin:
    def is_active(self) -> bool:
        """Go: Instance.IsActive. False for Stopped and Error."""
        return self.status_code not in (StatusCode.STOPPED, StatusCode.ERROR)  # type: ignore[attr-defined]
