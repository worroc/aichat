"""HTTPS for the hub and the UI: a private CA and a server certificate.

`aichat-server cert` writes four files to ``~/.config/aichat/tls``
(``AICHAT_TLS_DIR``):

* ``ca.pem``      the CA certificate. Clients trust this file. Copy it to
                  every client machine (``~/.config/aichat/tls/ca.pem`` or
                  ``$AICHAT_CA_CERT``).
* ``ca-key.pem``  the CA private key. It never leaves this host.
* ``cert.pem``    the server certificate, signed by the CA. Its SANs (the
                  names and IPs it is valid for) are this host's names and IPs.
* ``key.pem``     the server private key. It never leaves this host.

HTTPS is opt-in: the hub and the UI use it only when ``AICHAT_TLS_CERT`` and
``AICHAT_TLS_KEY`` are set. Reissuing the server certificate (new IPs) keeps
the CA, so clients keep working without a new copy.
"""

import datetime
import ipaddress
import json
import os
import socket
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_DIR = "~/.config/aichat/tls"
CA_DAYS = 3650
CERT_DAYS = 825  # the longest validity macOS and iOS accept for a TLS server certificate
CA_FILE, CA_KEY_FILE, CERT_FILE, KEY_FILE = "ca.pem", "ca-key.pem", "cert.pem", "key.pem"
_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_SKIP_SCOPES = {"link"}  # fe80::/10 and 169.254/16 addresses cannot be dialed from another host
_PROBE_TARGETS = (("8.8.8.8", socket.AF_INET), ("2001:4860:4860::8888", socket.AF_INET6))

SanName = x509.DNSName | x509.IPAddress
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class TlsError(Exception):
    """The TLS settings or files are wrong; the message says how to fix it."""


# --- settings -----------------------------------------------------------------


def tls_dir() -> Path:
    return Path(os.environ.get("AICHAT_TLS_DIR") or DEFAULT_DIR).expanduser()


def ca_path() -> Path | None:
    """The CA that clients of the hub trust: ``$AICHAT_CA_CERT``, else ``ca.pem`` in the TLS folder."""
    explicit = os.environ.get("AICHAT_CA_CERT")
    if explicit:
        return Path(explicit).expanduser()
    default = tls_dir() / CA_FILE
    return default if default.is_file() else None


def enabled(cert: str, key: str) -> bool:
    """True when both files are configured; raises when only one of them is."""
    if bool(cert) != bool(key):
        raise TlsError("set both AICHAT_TLS_CERT and AICHAT_TLS_KEY, or neither")
    return bool(cert)


def check(cert: str, key: str) -> None:
    """Fail early, with a readable message, when the configured files cannot be used."""
    if not enabled(cert, key):
        return
    try:
        ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(
            Path(cert).expanduser(), Path(key).expanduser()
        )
    except (OSError, ssl.SSLError) as exc:
        raise TlsError(f"cannot load AICHAT_TLS_CERT={cert} / AICHAT_TLS_KEY={key}: {exc}") from None


def probe_context() -> ssl.SSLContext:
    """For `start`/`status` only: it recognises our own service on a local port and sends
    no secret, so it does not verify the certificate (the bind address may not be in the SANs)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# --- host names and IPs --------------------------------------------------------


def _ip_command_addresses() -> set[str]:
    """Linux: every address of every interface, from `ip -j addr`."""
    try:
        out = subprocess.run(["ip", "-j", "addr", "show"], capture_output=True, text=True, check=True).stdout
        links = json.loads(out)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return set()
    infos = (info for link in links for info in link.get("addr_info", []))
    return {info["local"] for info in infos if info.get("scope") not in _SKIP_SCOPES and "local" in info}


def _route_address(target: str, family: socket.AddressFamily) -> str | None:
    """The source address the OS would use to reach `target`; a UDP connect sends no packet."""
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((target, 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _resolved_addresses() -> set[str]:
    """Fallback when `ip` is missing (macOS): the hostname's addresses and the default routes."""
    found = {_route_address(target, family) for target, family in _PROBE_TARGETS}
    try:
        found |= {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)}
    except OSError:
        pass
    return {addr for addr in found if addr}


def local_ips() -> set[IpAddress]:
    """This host's IP addresses that another host may dial, plus loopback."""
    raw = _ip_command_addresses() or _resolved_addresses()
    ips = {ipaddress.ip_address(addr.split("%")[0]) for addr in raw}
    return {ip for ip in ips if not ip.is_link_local}


def host_names() -> set[str]:
    return {name for name in (socket.gethostname(), socket.getfqdn()) if name}


def _san(value: str) -> SanName:
    try:
        return x509.IPAddress(ipaddress.ip_address(value))
    except ValueError:
        return x509.DNSName(value)


def default_sans(extra: list[str]) -> list[SanName]:
    """Loopback, this host's names and IPs, and `extra` (names or IPs), without duplicates."""
    values = [*_LOOPBACK, *sorted(host_names()), *sorted(map(str, local_ips())), *extra]
    found: dict[str, SanName] = {}
    for value in values:
        san = _san(value.strip())
        found.setdefault(str(san.value), san)
    return list(found.values())


def extra_sans_from_env() -> list[str]:
    return [v.strip() for v in os.environ.get("AICHAT_TLS_SANS", "").split(",") if v.strip()]


# --- files ----------------------------------------------------------------------


def _write(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _name(common_name: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "aichat"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])


def _builder(subject: x509.Name, issuer: x509.Name, public_key, days: int) -> x509.CertificateBuilder:
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
    )


def _usage(*, signs_certs: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=False,
        data_encipherment=False, key_agreement=False, key_cert_sign=signs_certs,
        crl_sign=signs_certs, encipher_only=False, decipher_only=False,
    )


def _make_ca(folder: Path) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = _name(f"aichat CA ({socket.gethostname()})")
    cert = (
        _builder(name, name, key.public_key(), CA_DAYS)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_usage(signs_certs=True), critical=True)
        .sign(key, hashes.SHA256())
    )
    _write(folder / CA_KEY_FILE, _key_pem(key), 0o600)
    _write(folder / CA_FILE, _cert_pem(cert), 0o644)


def _load_ca(folder: Path) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    cert = x509.load_pem_x509_certificate((folder / CA_FILE).read_bytes())
    key = serialization.load_pem_private_key((folder / CA_KEY_FILE).read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise TlsError(f"{folder / CA_KEY_FILE} is not an EC key made by `aichat-server cert`")
    return cert, key


def _make_server_cert(folder: Path, sans: list[SanName]) -> None:
    ca_cert, ca_key = _load_ca(folder)
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        _builder(_name(socket.gethostname()), ca_cert.subject, key.public_key(), CERT_DAYS)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_usage(signs_certs=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write(folder / KEY_FILE, _key_pem(key), 0o600)
    _write(folder / CERT_FILE, _cert_pem(cert), 0o644)


@dataclass(frozen=True)
class Made:
    new_ca: bool
    new_cert: bool


def make(folder: Path, extra_sans: list[str], *, force: bool = False, new_ca: bool = False) -> Made:
    """Create the CA if missing (or `new_ca`), and the server cert if missing (or `force`)."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    make_ca = new_ca or not (folder / CA_FILE).is_file() or not (folder / CA_KEY_FILE).is_file()
    if make_ca:
        _make_ca(folder)
    make_cert = make_ca or force or not (folder / CERT_FILE).is_file() or not (folder / KEY_FILE).is_file()
    if make_cert:
        _make_server_cert(folder, default_sans(extra_sans))
    return Made(make_ca, make_cert)


# --- report ---------------------------------------------------------------------


@dataclass(frozen=True)
class CertInfo:
    sans: list[str]
    expires: datetime.datetime


def read_cert(path: Path) -> CertInfo:
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        names = x509.SubjectAlternativeName([])
    return CertInfo([str(n.value) for n in names], cert.not_valid_after_utc)


def _url_host(value: str) -> str:
    return f"[{value}]" if ":" in value else value


def report(folder: Path, made: Made, hub_port: int, ui_port: int) -> str:
    """What was written, and the settings for the server and for remote clients."""
    info = read_cert(folder / CERT_FILE)
    ips = [s for s in info.sans if isinstance(_san(s), x509.IPAddress) and s not in _LOOPBACK]
    host = _url_host(ips[0]) if ips else "<host-ip>"
    state = {True: "created", False: "kept"}
    return "\n".join([
        f"CA ({state[made.new_ca]}):          {folder / CA_FILE}   <- copy this to every client",
        f"server cert ({state[made.new_cert]}): {folder / CERT_FILE}, key {folder / KEY_FILE}",
        f"valid for: {', '.join(info.sans)}",
        f"expires:   {info.expires:%Y-%m-%d}",
        "",
        "on this host (hub and UI), before `aichat-server start`:",
        f"  export AICHAT_TLS_CERT={folder / CERT_FILE}",
        f"  export AICHAT_TLS_KEY={folder / KEY_FILE}",
        "  export AICHAT_HOST=0.0.0.0 AICHAT_UI_HOST=0.0.0.0   # to accept remote clients",
        f"  export AICHAT_URL=https://127.0.0.1:{hub_port}",
        "",
        f"on a client: copy {CA_FILE} to ~/.config/aichat/tls/{CA_FILE} (or set AICHAT_CA_CERT), then",
        f"  export AICHAT_URL=https://{host}:{hub_port}   # or another IP above",
        f"browser: trust {CA_FILE} once, then open https://{host}:{ui_port}",
    ])
