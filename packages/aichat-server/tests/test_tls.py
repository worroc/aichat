"""HTTPS: the private CA, the server certificate, and the hub served over TLS."""

import socket
import ssl
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from aichat_server import tls, ui_dev
from aichat_server.config import Settings
from aichat_server.lifecycle import LifecycleError, probe
from aichat_server.main import _check_tls, create_app, hub_service
from cryptography import x509


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def certs(tmp_path) -> Path:
    tls.make(tmp_path, ["203.0.113.7", "hub.example.test"])
    return tmp_path


@pytest.fixture
def tls_hub(certs):
    port = _free_port()
    settings = Settings(operator_key="k", store="memory", port=port,
                        tls_cert=str(certs / "cert.pem"), tls_key=str(certs / "key.pem"))
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning",
                            ssl_certfile=settings.tls_cert, ssl_keyfile=settings.tls_key)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    yield settings
    server.should_exit = True


def _trusting(ca: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(ca))  # strict X.509 checks on Python 3.13+


def test_cert_names_loopback_host_and_extra(certs):
    sans = tls.read_cert(certs / "cert.pem").sans
    assert {"localhost", "127.0.0.1", "::1", "203.0.113.7", "hub.example.test"} <= set(sans)
    assert socket.gethostname() in sans
    assert len(sans) == len(set(sans))


def test_keys_are_private(certs):
    assert (certs / "key.pem").stat().st_mode & 0o777 == 0o600
    assert (certs / "ca-key.pem").stat().st_mode & 0o777 == 0o600


def test_server_cert_is_signed_by_the_ca(certs):
    ca = x509.load_pem_x509_certificate((certs / "ca.pem").read_bytes())
    cert = x509.load_pem_x509_certificate((certs / "cert.pem").read_bytes())
    cert.verify_directly_issued_by(ca)
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca


def test_make_keeps_existing_files(certs):
    before = (certs / "ca.pem").read_bytes(), (certs / "cert.pem").read_bytes()
    made = tls.make(certs, [])
    assert made == tls.Made(new_ca=False, new_cert=False)
    assert ((certs / "ca.pem").read_bytes(), (certs / "cert.pem").read_bytes()) == before


def test_force_reissues_cert_and_keeps_ca(certs):
    ca, cert = (certs / "ca.pem").read_bytes(), (certs / "cert.pem").read_bytes()
    assert tls.make(certs, ["198.51.100.1"], force=True) == tls.Made(new_ca=False, new_cert=True)
    assert (certs / "ca.pem").read_bytes() == ca
    assert (certs / "cert.pem").read_bytes() != cert
    assert "198.51.100.1" in tls.read_cert(certs / "cert.pem").sans


def test_new_ca_replaces_both(certs):
    ca = (certs / "ca.pem").read_bytes()
    assert tls.make(certs, [], new_ca=True) == tls.Made(new_ca=True, new_cert=True)
    assert (certs / "ca.pem").read_bytes() != ca


def test_hub_over_https_trusted_by_the_ca(tls_hub, certs):
    for host in ("127.0.0.1", "localhost"):
        resp = httpx.get(f"https://{host}:{tls_hub.port}/health", verify=_trusting(certs / "ca.pem"))
        assert resp.json() == {"ok": True}


def test_hub_over_https_rejected_without_the_ca(tls_hub):
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"https://127.0.0.1:{tls_hub.port}/health")


def test_probe_sees_tls_hub(tls_hub):
    svc = hub_service(tls_hub)
    assert svc.url.startswith("https://")
    assert probe(svc) == "ours"


def test_only_cert_or_only_key_is_refused(certs):
    with pytest.raises(LifecycleError, match="both"):
        _check_tls(Settings(operator_key="k", tls_cert=str(certs / "cert.pem")))


def test_missing_cert_file_is_refused(tmp_path):
    with pytest.raises(LifecycleError, match="cannot load"):
        _check_tls(Settings(operator_key="k", tls_cert=str(tmp_path / "no.pem"), tls_key=str(tmp_path / "no.pem")))


def test_ui_reads_hub_tls_env_and_passes_pem_to_vite(certs, monkeypatch):
    monkeypatch.setenv("AICHAT_TLS_CERT", str(certs / "cert.pem"))
    monkeypatch.setenv("AICHAT_TLS_KEY", str(certs / "key.pem"))
    monkeypatch.setenv("AICHAT_CA_CERT", str(certs / "ca.pem"))
    monkeypatch.delenv("AICHAT_URL", raising=False)
    settings = ui_dev.UiSettings()
    assert ui_dev.ui_service(settings).url.startswith("https://")
    env = ui_dev.tls_env(settings)
    assert env["AICHAT_UI_TLS_CERT_PEM"] == (certs / "cert.pem").read_text()
    assert env["NODE_EXTRA_CA_CERTS"] == str(certs / "ca.pem")
    assert env["AICHAT_URL"].startswith("https://")


def test_ui_without_tls_is_plain_http(monkeypatch):
    for var in ("AICHAT_TLS_CERT", "AICHAT_TLS_KEY"):
        monkeypatch.delenv(var, raising=False)
    settings = ui_dev.UiSettings()
    assert ui_dev.ui_service(settings).url.startswith("http://")
    assert ui_dev.tls_env(settings) == {}
