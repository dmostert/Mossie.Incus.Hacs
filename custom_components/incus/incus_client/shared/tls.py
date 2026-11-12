"""Go: shared/tls (certificate helpers). Certificate generation needs the optional ``cryptography`` package."""
from __future__ import annotations

import hashlib
import socket
import ssl
from urllib.parse import urlsplit


def cert_fingerprint_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def cert_fingerprint_str(pem: str) -> str:
    """Go: CertFingerprintStr. Lower-case hex SHA-256 of the DER certificate (input is PEM)."""
    return cert_fingerprint_der(ssl.PEM_cert_to_DER_cert(pem.strip()))


def generate_mem_cert(client: bool, add_hosts: bool) -> tuple[str, str]:
    """Go: GenerateMemCert. Self-signed ECDSA P-384 certificate valid for 10 years. Returns (cert_pem, key_pem);
    the key is SEC1 ("EC PRIVATE KEY"). With ``add_hosts`` the hostname and local IPs become subject alternative names."""
    try:
        import datetime, getpass, ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("generate_mem_cert needs the 'cryptography' package (pip install cryptography)") from e
    host = socket.gethostname()
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "unknown"
    key = ec.generate_private_key(ec.SECP384R1())
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Linux Containers"),
                      x509.NameAttribute(NameOID.COMMON_NAME, f"{user}@{host}")])
    now = datetime.datetime.now(datetime.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
         .not_valid_after(now + datetime.timedelta(days=3650))
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH]), critical=False))
    if add_hosts:
        sans: list = [x509.DNSName(host), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        try:
            for info in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(info[4][0])
                if x509.IPAddress(ip) not in sans:
                    sans.append(x509.IPAddress(ip))
        except (socket.gaierror, ValueError):
            pass
        b = b.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    cert = b.sign(key, hashes.SHA384())
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()).decode())


def get_remote_certificate(address: str, timeout: float | None = 10.0) -> str:
    """Go: GetRemoteCertificate. TLS handshake without validation; returns the server's leaf certificate as PEM."""
    parts = urlsplit(address if "://" in address else "https://" + address)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((parts.hostname, parts.port or 8443), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=parts.hostname) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise OSError("The server didn't present a certificate")
    return ssl.DER_cert_to_PEM_cert(der)
