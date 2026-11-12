"""Builds an Incus client from a config entry's data and provides config-flow helpers. Home-Assistant-independent, blocking."""
from __future__ import annotations

import http.client
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from .const import (CONF_CLIENT_CERT, CONF_CLIENT_KEY, CONF_CONNECTION, CONF_SERVER_CERT, CONF_SOCKET, CONF_URL, CONF_VERIFY,
                    CONN_UNIX, DEFAULT_SOCKET, REQUEST_TIMEOUT, VERIFY_SKIP, VERIFY_SYSTEM)
from .incus_client import ConnectionArgs, IncusError, connect_incus, connect_incus_unix, models
from .incus_client.shared import tls


_LOGGER = logging.getLogger(__name__)
_NET_ERRORS = (OSError, ValueError, http.client.HTTPException)   # ValueError: malformed PEM pasted into the form


def describe(err: BaseException) -> str:
    """Short, human-readable reason (what the user needs to see to fix the problem)."""
    return getattr(err, "message", None) or f"{type(err).__name__}: {err}"


class CannotConnect(Exception):
    """The server can't be reached or the TLS handshake failed."""


class NotTrusted(Exception):
    """Connected, but the server doesn't trust our client certificate."""


class InvalidToken(Exception):
    """The trust token was rejected."""


@dataclass(frozen=True)
class ServerInfo:
    name: str
    version: str | None
    fingerprint: str | None
    auth: str | None


def normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    return value


def connection_args(data: Mapping[str, Any]) -> ConnectionArgs:
    verify = data.get(CONF_VERIFY)
    return ConnectionArgs(
        tls_server_cert=data.get(CONF_SERVER_CERT) or "" if verify not in (VERIFY_SYSTEM, VERIFY_SKIP) else "",
        tls_client_cert=data.get(CONF_CLIENT_CERT) or "", tls_client_key=data.get(CONF_CLIENT_KEY) or "",
        insecure_skip_verify=verify == VERIFY_SKIP, timeout=REQUEST_TIMEOUT, user_agent="home-assistant-incus")


def build_client(data: Mapping[str, Any], *, skip_get_server: bool = False):
    args = connection_args(data)
    args.skip_get_server = skip_get_server
    if data.get(CONF_CONNECTION) == CONN_UNIX:
        return connect_incus_unix(data.get(CONF_SOCKET) or DEFAULT_SOCKET, args)
    return connect_incus(normalize_url(data[CONF_URL]), args)


def fetch_server_certificate(url: str) -> str:
    """TLS handshake without validation; returns the server certificate (PEM) so it can be pinned."""
    try:
        return tls.get_remote_certificate(normalize_url(url), timeout=10)
    except _NET_ERRORS as e:
        _LOGGER.warning("Cannot fetch the TLS certificate of %s: %s", url, describe(e))
        raise CannotConnect(describe(e)) from e


def generate_client_identity() -> tuple[str, str]:
    """A fresh self-signed client certificate and key (PEM)."""
    return tls.generate_mem_cert(client=True, add_hosts=False)


def register_with_token(data: Mapping[str, Any], token: str, client_name: str) -> None:
    """Adds our client certificate to the server's trust store using a token from ``incus config trust add``."""
    try:
        client = build_client(data, skip_get_server=True)
        client.create_certificate(models.CertificatesPost(name=client_name, type="client", trust_token=token.strip()))
    except IncusError as e:
        _LOGGER.warning("Registering the client certificate failed (HTTP %s): %s", e.status_code, e.message)
        if e.status_code in (400, 401, 403):
            raise InvalidToken(e.message) from e
        raise CannotConnect(describe(e)) from e
    except _NET_ERRORS as e:
        _LOGGER.warning("Registering the client certificate failed: %s", describe(e))
        raise CannotConnect(describe(e)) from e


def validate_connection(data: Mapping[str, Any]) -> ServerInfo:
    """Connects, reads the server record and checks we are trusted. Raises CannotConnect / NotTrusted."""
    try:
        client = build_client(data)
        server = client.get_server()[0]
    except (IncusError, *_NET_ERRORS) as e:
        _LOGGER.warning("Cannot connect to Incus (%s): %s", data.get(CONF_URL) or data.get(CONF_SOCKET), describe(e))
        raise CannotConnect(describe(e)) from e
    if server.auth != "trusted":
        _LOGGER.warning("Connected to Incus but the server reports auth=%r: our client certificate is not trusted", server.auth)
        raise NotTrusted(server.auth or "untrusted")
    env = server.environment
    fingerprint = tls.cert_fingerprint_str(env.certificate) if env and env.certificate else None
    return ServerInfo(name=(env.server_name if env and env.server_name else None) or "incus",
                      version=env.server_version if env else None, fingerprint=fingerprint, auth=server.auth)
