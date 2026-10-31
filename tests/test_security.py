from unittest.mock import MagicMock, patch

from joki.tools.security import (
    handle_api_discover,
    handle_cve_search,
    handle_dir_bruteforce,
    handle_dns_enum,
    handle_form_analyze,
    handle_port_scan,
    handle_source_map_check,
    handle_ssl_check,
    handle_tech_detect,
    handle_web_vuln_scan,
    handle_whois_lookup,
)

# ---------- target_authorized ----------

def test_port_scan_without_authorization_rejected():
    result = handle_port_scan({"target": "127.0.0.1"})
    assert "target_authorized" in result


def test_port_scan_with_authorization_runs():
    with patch("joki.tools.security.socket.socket") as mock_sock:
        inst = mock_sock.return_value
        inst.connect_ex.return_value = 1
        result = handle_port_scan({
            "target": "127.0.0.1", "target_authorized": True,
            "ports": "common", "scan_type": "quick"})
        assert "No open ports" in result


def test_web_vuln_scan_without_authorization_rejected():
    result = handle_web_vuln_scan({"url": "http://example.com"})
    assert "target_authorized" in result


def test_dir_bruteforce_without_authorization_rejected():
    result = handle_dir_bruteforce({"url": "http://example.com"})
    assert "target_authorized" in result


# ---------- happy paths (semua mock) ----------

def test_web_vuln_scan_runs(monkeypatch):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 200
            self.headers = {"Server": "nginx", "Content-Type": "text/html"}
            self.content = b"hello"
            self.text = "<html>hello</html>"
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    result = handle_web_vuln_scan({
        "url": "http://example.com", "target_authorized": True,
        "checks": "headers,info"})
    assert "[WEB_VULN]" in result


def test_dir_bruteforce_runs(monkeypatch):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 404
            self.content = b"x"
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    result = handle_dir_bruteforce({
        "url": "http://example.com", "target_authorized": True,
        "wordlist": "small"})
    assert "[DIRBRUTE]" in result
    assert "No paths found" in result


def _brute_paths(monkeypatch, **kwargs):
    """Jalankan dir_bruteforce dengan HTTP mock, kembalikan daftar path yang di-request."""
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 404
            self.content = b"x"
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return FakeResp()
    monkeypatch.setattr("joki.tools.security.httpx.get", fake_get)
    args = {"url": "http://example.com", "target_authorized": True}
    args.update(kwargs)
    result = handle_dir_bruteforce(args)
    assert "[DIRBRUTE]" in result
    return seen


def test_dir_bruteforce_wordlists_cumulative(monkeypatch):
    """Regresi: large harus memuat small+medium (dulu large dibangun dari
    list medium yang masih kosong sehingga tak pernah jalan penuh)."""
    small = _brute_paths(monkeypatch, wordlist="small")
    medium = _brute_paths(monkeypatch, wordlist="medium")
    large = _brute_paths(monkeypatch, wordlist="large")
    assert len(small) > 0
    assert len(medium) > len(small)
    assert len(large) > len(medium)
    for p in small:
        assert p in medium and p in large
    for p in medium:
        assert p in large
    assert any(p.endswith("/admin") for p in large)
    assert any(p.endswith("/dashboard") for p in large)
    assert any(p.endswith("/docker") for p in large)
    assert not any(p.endswith("/dashboard") for p in small)
    assert not any(p.endswith("/docker") for p in small + medium)
    # Tanpa duplikat request.
    assert len(large) == len(set(large))


def test_dir_bruteforce_unknown_size_falls_back_to_small(monkeypatch):
    small = _brute_paths(monkeypatch, wordlist="small")
    other = _brute_paths(monkeypatch, wordlist="raksasa")
    assert small == other


def test_dns_enum_records(monkeypatch):
    def fake_run(cmd, **kw):
        r = MagicMock()
        r.stdout = "93.184.216.34\n"
        r.returncode = 0
        return r
    monkeypatch.setattr("joki.tools.security.subprocess.run", fake_run)
    result = handle_dns_enum({"domain": "example.com", "action": "records"})
    assert "example.com" in result
    assert "93.184.216.34" in result


def test_ssl_check(monkeypatch):
    class FakeSSock:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def version(self):
            return "TLSv1.3"
        def getpeercert(self):
            return {}
    class FakeCtx:
        def wrap_socket(self, sock, server_hostname=None):
            return FakeSSock()
    class FakeSock:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(
        "joki.tools.security.socket.create_connection",
        lambda *a, **kw: FakeSock())
    monkeypatch.setattr(
        "joki.tools.security.ssl.create_default_context",
        lambda: FakeCtx())
    result = handle_ssl_check({"host": "example.com"})
    assert "example.com" in result


def test_whois_lookup(monkeypatch):
    def fake_run(cmd, **kw):
        r = MagicMock()
        r.stdout = "   Domain Name: EXAMPLE.COM\n   Registrar: Example Registrar\n"
        r.stderr = ""
        return r
    monkeypatch.setattr("joki.tools.security.subprocess.run", fake_run)
    result = handle_whois_lookup({"target": "example.com"})
    assert "EXAMPLE.COM" in result


def test_cve_search(monkeypatch):
    class FakeResp:
        status_code = 200
        def json(self):
            return [{"id": "CVE-2024-1234", "summary": "test",
                     "cvss_score": 9.8, "severity": "Critical"}]
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    result = handle_cve_search({"query": "apache 2.4"})
    assert "CVE-2024-1234" in result


def test_tech_detect(monkeypatch):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 200
            self.headers = {"Server": "nginx"}
            self.text = "<title>Test</title><script src=x.js></script>"
            self.cookies = []
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    result = handle_tech_detect({"url": "http://example.com", "deep": "simple"})
    assert "[TECH]" in result


def test_api_discover(monkeypatch):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 200
            self.text = '<form action="/login"></form>'
            self.headers = {}
            self.cookies = []
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    result = handle_api_discover({"url": "http://example.com"})
    assert "[API]" in result


def test_source_map_check(monkeypatch):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 200
            self.text = '<script src="app.js"></script>'
            self.headers = {}
            self.cookies = []
    class FakeHead:
        def __init__(self, *a, **kw):
            self.status_code = 404
            self.text = ""
            self.content = b""
            self.headers = {}
            self.cookies = []
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    monkeypatch.setattr("joki.tools.security.httpx.head", lambda *a, **kw: FakeHead())
    result = handle_source_map_check({"url": "http://example.com"})
    assert "[SOURCEMAP]" in result
    assert "No source maps found" in result


def test_form_analyze(monkeypatch):
    class FakeResp:
        def __init__(self, *a, **kw):
            self.status_code = 200
            self.text = ('<form action="/submit" method="post">'
                         '<input type="hidden" name="_token" value="abc"></form>')
            self.headers = {}
            self.cookies = []
    monkeypatch.setattr("joki.tools.security.httpx.get", lambda *a, **kw: FakeResp())
    result = handle_form_analyze({"url": "http://example.com/login"})
    assert "[FORM]" in result
    assert "CSRF" in result
