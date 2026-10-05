"""HTTPS to the registration website: the certificate fallback and the
diagnostics, against a local HTTPS server with its own test certificate."""

import http.server
import json
import shutil
import ssl
import subprocess
import threading

import pytest

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    d = tmp_path_factory.mktemp("certs")
    run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-subj", "/CN=Presentia Test CA", "-keyout", "ca.key", "-out", "ca.pem")
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
        "-keyout", "site.key", "-out", "site.csr")
    run("openssl", "x509", "-req", "-in", "site.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
        "-CAcreateserial", "-days", "2", "-extfile", "ext.cnf", "-out", "site.pem")
    return d


@pytest.fixture
def site(certs, fresh_db, monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"ok": True, "version": "test"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certs / "site.pem", certs / "site.key")
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    from app.data import cloud

    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("PRESENTIA_CLOUD_URL", f"https://localhost:{httpd.server_port}")
    monkeypatch.setattr(cloud, "_ctx_cache", {})
    monkeypatch.setattr(cloud, "_working", None)
    yield cloud, certs
    httpd.shutdown()


def _trust_test_ca_as_builtin(cloud, certs, monkeypatch):
    """Windows' check (truststore) does not know the test CA; the 'built-in
    list' does — like a laptop whose Windows root store is out of date."""
    monkeypatch.setattr(cloud, "TRUST_OPTIONS", [
        (cloud.TRUST_OPTIONS[0][0], cloud._system_ctx),
        (cloud.TRUST_OPTIONS[1][0], lambda: ssl.create_default_context(cafile=str(certs / "ca.pem"))),
        (cloud.TRUST_OPTIONS[2][0], cloud._python_ctx),
    ])


def test_falls_back_to_builtin_list(site, monkeypatch):
    cloud, certs = site
    from app.core import diag

    _trust_test_ca_as_builtin(cloud, certs, monkeypatch)
    assert cloud._request("GET", "/api/health", auth=False)["ok"] is True
    assert cloud.trust_in_use() == cloud.TRUST_OPTIONS[1][0]
    assert any("rejected the website's certificate" in e["message"] for e in diag.recent())
    # The working option is remembered and tried first next time.
    assert cloud._request("GET", "/api/health", auth=False)["ok"] is True


def test_unverifiable_certificate_is_refused(site):
    cloud, _ = site
    with pytest.raises(cloud.CloudError) as err:
        cloud._request("GET", "/api/health", auth=False)
    assert "security certificate" in err.value.message


def test_diagnose_points_at_the_certificate(site, monkeypatch):
    cloud, certs = site
    _trust_test_ca_as_builtin(cloud, certs, monkeypatch)
    out = cloud.diagnose(timeout=5)
    status = {c["id"]: c["status"] for c in out["checks"]}
    assert status["dns"] == status["tcp"] == status["health"] == status["clock"] == "ok"
    assert status[f"tls:{cloud.TRUST_OPTIONS[0][0]}"] == "fail"
    assert status[f"tls:{cloud.TRUST_OPTIONS[1][0]}"] == "ok"
    assert any("uses that automatically" in h for h in out["hints"])


def test_diagnose_unreachable(fresh_db, monkeypatch):
    from app.data import cloud

    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("PRESENTIA_CLOUD_URL", "https://127.0.0.1:9")
    out = cloud.diagnose(timeout=2)
    status = {c["id"]: c["status"] for c in out["checks"]}
    assert status["tcp"] == "fail" and status["health"] == "fail"


def test_diagnostics_endpoint(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import sidecar

    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("PRESENTIA_CLOUD_URL", "https://127.0.0.1:9")
    res = TestClient(sidecar.app).get("/api/diagnostics")
    assert res.status_code == 200
    body = res.json()
    assert {r["label"] for r in body["system"]} >= {"Python", "Data folder", "Face engine", "certifi"}
    assert body["network"]["checks"] and isinstance(body["recent"], list)
