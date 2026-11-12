#!/usr/bin/env python3
"""Checks, step by step, whether this machine can talk to an Incus server - the same calls the Home Assistant integration makes.

Copy it to your HA config directory (next to custom_components/) and run it where Home Assistant runs
(HA OS: "Terminal & SSH" add-on, then `docker exec -it homeassistant sh`; Docker: `docker exec -it homeassistant sh`):

  python3 diagnose.py https://incus-host:8443                       # network + TLS + (untrusted) server info
  python3 diagnose.py https://incus-host:8443 --cert c.crt --key c.key   # authenticated: instances and storage
  python3 diagnose.py --socket /var/lib/incus/unix.socket
Add -v for the client's debug log.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import socket
import ssl
import sys
import types
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))


def find_pkg_dir() -> str:
    """Locate custom_components/incus: next to this script, one level up (repo layout), or in Home Assistant's /config."""
    for base in (HERE, os.path.dirname(HERE), "/config", os.getcwd()):
        cand = os.path.join(base, "custom_components", "incus")
        if os.path.isdir(cand):
            return cand
    sys.exit("Cannot find custom_components/incus (copy this script into your HA config directory, e.g. /config/diagnose.py)")


PKG_DIR = find_pkg_dir()
ROOT = os.path.dirname(os.path.dirname(PKG_DIR))


def load(name: str):
    """Import integration modules without importing Home Assistant (the package __init__ needs it)."""
    if "custom_components.incus" not in sys.modules:
        cc = types.ModuleType("custom_components")
        cc.__path__ = [os.path.join(ROOT, "custom_components")]
        inc = types.ModuleType("custom_components.incus")
        inc.__path__ = [PKG_DIR]
        sys.modules["custom_components"], sys.modules["custom_components.incus"] = cc, inc
    return importlib.import_module(f"custom_components.incus.{name}")


def ok(msg): print(f"  [ OK ] {msg}")
def bad(msg, hint=None):
    print(f"  [FAIL] {msg}")
    if hint:
        print(f"         -> {hint}")


def hint_for(err: BaseException) -> str:
    if isinstance(err, ConnectionRefusedError):
        return "Nothing listens on that port. Enable remote access on the server: `incus config set core.https_address :8443`."
    if isinstance(err, (socket.timeout, TimeoutError)):
        return "Timed out: a firewall is dropping the connection, the address is wrong, or the host is unreachable from here."
    if isinstance(err, socket.gaierror):
        return "The host name cannot be resolved from here; try the IP address."
    if isinstance(err, FileNotFoundError):
        return "The socket path doesn't exist in this environment (inside a container the socket must be mounted)."
    if isinstance(err, PermissionError):
        return "No permission on the socket (it is owned by root:incus-admin by default)."
    if isinstance(err, ssl.SSLCertVerificationError):
        return "Certificate not trusted: use the pinned certificate (default in the integration) or --insecure."
    if isinstance(err, ssl.SSLError):
        return "TLS handshake failed (wrong port or not an Incus HTTPS endpoint? bad client certificate/key?)."
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="https://host:8443")
    ap.add_argument("--socket", help="Unix socket path instead of a URL")
    ap.add_argument("--cert", help="client certificate PEM file")
    ap.add_argument("--key", help="client private key PEM file")
    ap.add_argument("--insecure", action="store_true", help="do not verify/pin the server certificate")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    if not a.url and not a.socket:
        ap.error("give a URL or --socket")
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING, format="        %(name)s: %(message)s")

    factory, fetch = load("client_factory"), load("fetch")
    data: dict = {}
    failures = 0

    if a.socket:
        print(f"Unix socket {a.socket}")
        data = {"connection": "unix", "socket_path": a.socket}
        if os.path.exists(a.socket):
            ok("socket exists")
        else:
            bad("socket does not exist", hint_for(FileNotFoundError()))
            return 1
    else:
        url = factory.normalize_url(a.url)
        u = urlsplit(url)
        host, port = u.hostname, u.port or 8443
        print(f"{url}")
        if u.scheme != "https":
            bad(f"scheme is {u.scheme!r}", "Incus remote access is HTTPS only (default port 8443).")
            return 1
        try:
            addr = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]
            ok(f"{host} resolves to {addr}")
            socket.create_connection((host, port), timeout=5).close()
            ok(f"TCP connection to port {port}")
        except OSError as e:
            bad(f"{type(e).__name__}: {e}", hint_for(e))
            return 1
        try:
            pem = factory.fetch_server_certificate(url)
            fp = factory.tls.cert_fingerprint_str(pem)
            ok(f"TLS handshake; server certificate fingerprint {fp}  (compare with `incus info`)")
        except factory.CannotConnect as e:
            bad(str(e), hint_for(getattr(e, "__cause__", None) or e))
            return 1
        data = {"connection": "https", "url": url, "verify": "skip" if a.insecure else "pin", "server_cert": pem}
        if a.cert and a.key:
            data["client_cert"], data["client_key"] = open(a.cert).read(), open(a.key).read()
            ok("client certificate loaded")

    try:
        client = factory.build_client(data)
        srv = client.get_server()[0]
    except Exception as e:  # noqa: BLE001
        bad(f"{type(e).__name__}: {getattr(e, 'message', e)}", hint_for(e))
        return 1
    env = srv.environment
    ok(f"GET /1.0: server {env.server_name if env else '?'}, version {env.server_version if env else '?'}, auth={srv.auth!r}")
    if srv.auth != "trusted":
        bad("the server does not trust this client",
            "Run `incus config trust add home-assistant` on the server and use the token in the integration "
            "(or `incus config trust add-certificate <client.crt>`).")
        return 1

    for label, call in (("instances", lambda: fetch.fetch_instances(client, ["*"])),
                        ("storage pools/volumes", lambda: fetch.fetch_volumes(client, ["*"]))):
        try:
            r = call()
            ok(f"{label}: " + (f"{len(r)} found" if isinstance(r, list) else f"{len(r.pools)} pools, {len(r.volumes)} custom volumes"))
        except Exception as e:  # noqa: BLE001
            failures += 1
            bad(f"{label}: {type(e).__name__}: {getattr(e, 'message', e)}",
                "Permission problem? A restricted certificate only sees its allowed projects." if "ermission" in str(e) else "")
    print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
