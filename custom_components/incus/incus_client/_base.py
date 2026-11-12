"""ApiObject base class: JSON <-> dataclass conversion that keeps unknown properties in ``extra``."""
from __future__ import annotations

import re
import sys
import types
import typing
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any

_FRAC = re.compile(r"\.(\d+)")


def parse_time(s: str) -> datetime:
    """Parses RFC 3339 as emitted by Go (nanosecond fractions, 'Z', offsets, year-1 zero time)."""
    s = _FRAC.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def convert(value: Any, hint: Any) -> Any:
    """Converts decoded JSON into the type described by ``hint`` (models, lists, dicts, datetimes)."""
    if value is None:
        return None
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        return convert(value, args[0]) if len(args) == 1 else value
    if origin is list:
        (inner,) = typing.get_args(hint)
        return [convert(v, inner) for v in value]
    if origin is dict:
        _, inner = typing.get_args(hint)
        return {k: convert(v, inner) for k, v in value.items()}
    if isinstance(hint, type):
        if issubclass(hint, ApiObject):
            return hint.from_dict(value)
        if hint is datetime and isinstance(value, str):
            return parse_time(value)
    return value


def _dump(value: Any) -> Any:
    if isinstance(value, ApiObject):
        return value.to_dict()
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass(kw_only=True)
class ApiObject:
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    _WRITABLE: typing.ClassVar[str | None] = None

    @classmethod
    def from_dict(cls, data: dict | None):
        if data is None:
            return None
        hints = typing.get_type_hints(cls)
        by_json = {f.metadata.get("json", f.name): f for f in fields(cls) if f.name != "extra"}
        kwargs, extra = {}, {}
        for key, val in data.items():
            f = by_json.get(key)
            if f is None:
                extra[key] = val
            else:
                kwargs[f.name] = convert(val, hints[f.name])
        obj = cls(**kwargs)
        obj.extra = extra
        return obj

    def to_dict(self) -> dict:
        """JSON-ready dict. ``None`` values are omitted (like C#'s WhenWritingNull); unknown properties are kept."""
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "extra":
                continue
            v = getattr(self, f.name)
            if v is not None:
                out[f.metadata.get("json", f.name)] = _dump(v)
        for k, v in self.extra.items():
            out.setdefault(k, v)
        return out

    def writable(self):
        """Go: Writable(). The ``*Put`` view of this resource (shares nested dicts/lists with it)."""
        if not self._WRITABLE:
            raise NotImplementedError(f"{type(self).__name__} has no writable view")
        put_cls = getattr(sys.modules[type(self).__module__], self._WRITABLE)
        own = {f.name for f in fields(self)}
        names = [f.name for f in fields(put_cls) if f.name in own and f.name not in ("extra", "restore")]
        return put_cls(**{n: getattr(self, n) for n in names})

    def set_writable(self, put) -> None:
        """Go: SetWritable()."""
        own = {f.name for f in fields(self)}
        for f in fields(put):
            if f.name in own and f.name not in ("extra", "restore"):
                setattr(self, f.name, getattr(put, f.name))
