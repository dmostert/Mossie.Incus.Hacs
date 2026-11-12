"""Go: shared/validate (representative subset). A validator takes a string and returns ``None`` if valid, else an error message."""
from __future__ import annotations

import ipaddress
from typing import Callable

Validator = Callable[[str], "str | None"]


def is_any(value: str) -> str | None:
    return None


def is_not_empty(value: str) -> str | None:
    return "Required value" if value == "" else None


def optional(*checks: Validator) -> Validator:
    """Empty values are valid; otherwise all ``checks`` must pass."""
    def v(value: str) -> str | None:
        if value == "":
            return None
        for c in checks:
            err = c(value)
            if err:
                return err
        return None
    return v


def is_one_of(*allowed: str) -> Validator:
    return lambda value: None if value in allowed else f"Invalid value {value} (must be one of: {', '.join(allowed)})"


def is_bool(value: str) -> str | None:
    from .util import is_false, is_true
    return None if is_true(value) or is_false(value) else f'Invalid value for a boolean "{value}"'


def _int_in(value: str, lo: int, hi: int, what: str) -> str | None:
    try:
        n = int(value)
    except ValueError:
        return f'Invalid value for {what} "{value}"'
    return None if lo <= n <= hi else f'Invalid value for {what} "{value}"'


def is_int64(value: str) -> str | None:
    return _int_in(value, -(1 << 63), (1 << 63) - 1, "an integer")


def is_uint8(value: str) -> str | None:
    return _int_in(value, 0, 255, "an unsigned 8-bit integer")


def is_uint32(value: str) -> str | None:
    return _int_in(value, 0, (1 << 32) - 1, "an unsigned 32-bit integer")


def is_uint64(value: str) -> str | None:
    return _int_in(value, 0, (1 << 64) - 1, "an unsigned 64-bit integer")


def is_network_port(value: str) -> str | None:
    return _int_in(value, 0, 65535, "a port")


def _ip(value: str, version: int | None):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    return ip if version is None or ip.version == version else None


def is_network_address(value: str) -> str | None:
    return None if _ip(value, None) else f'Not an IP address "{value}"'


def is_network_address_v4(value: str) -> str | None:
    return None if _ip(value, 4) else f'Not an IPv4 address "{value}"'


def is_network_address_v6(value: str) -> str | None:
    return None if _ip(value, 6) else f'Not an IPv6 address "{value}"'


def _cidr(value: str, version: int, strict: bool):
    try:
        net = ipaddress.ip_interface(value) if not strict else ipaddress.ip_network(value, strict=True)
    except ValueError:
        return None
    return net if net.version == version and "/" in value else None


def is_network_address_cidr_v4(value: str) -> str | None:
    return None if _cidr(value, 4, False) else f'Invalid IPv4 CIDR "{value}"'


def is_network_address_cidr_v6(value: str) -> str | None:
    return None if _cidr(value, 6, False) else f'Invalid IPv6 CIDR "{value}"'


def is_network_v4(value: str) -> str | None:
    """Network in CIDR notation, host bits must be zero (10.0.0.0/24)."""
    return None if _cidr(value, 4, True) else f'Invalid IPv4 network "{value}"'


def is_network_v6(value: str) -> str | None:
    return None if _cidr(value, 6, True) else f'Invalid IPv6 network "{value}"'


def is_url_segment_safe(name: str) -> str | None:
    return f'Name cannot contain any of / \\ ? # % ("{name}")' if any(c in name for c in "/\\?#%") else None


def is_list_of(item: Validator) -> Validator:
    """Comma-separated list where every item satisfies ``item``."""
    def v(value: str) -> str | None:
        for s in (x.strip() for x in value.split(",")):
            err = item(s)
            if err:
                return err
        return None
    return v


def check(validator: Validator, value: str) -> None:
    """Raises ValueError if the validator rejects the value."""
    err = validator(value)
    if err:
        raise ValueError(err)
