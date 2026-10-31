import re
import socket
import ssl
import subprocess

import httpx
from duckduckgo_search import DDGS

from joki.display import _Spinner
from joki.state import _console


def _http_verify(args):
    return not args.get("skip_ssl_verify", False)


def _require_target_authorization(args, tool_name):
    # Persetujuan manusia via cli (approve_once / sesi) mengalahkan flag AI.
    # Bersihkan forgery internal dulu, lalu cek sesi/sekali-pakai sebelum
    # menuntut target_authorized=true (fallback langsung tanpa cli).
    try:
        import joki.state as _st
        try:
            args.pop(_st._HUMAN_APPROVAL_KEY, None)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            if _st.is_session_allowed("nonlocal_scan"):
                return None
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            if _st.consume_once_approval(tool_name, args):
                return None
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception:  # noqa: BLE001, S110
        pass
    if args.get("target_authorized") is not True:
        return (f"Error: {tool_name} butuh otorisasi target. "
                f"Pastikan Anda memiliki izin untuk scan target ini. "
                f"Di alur agent, persetujuan manusia (Y/N terminal atau dialog TUI) "
                f"cukup sekali — eksekusi langsung jalan tanpa panggil ulang. "
                f"Untuk panggilan langsung, set target_authorized=true untuk melanjutkan.")
    return None


def handle_port_scan(args):
    target = args.get("target", "")
    if not target:
        return "Error: Parameter 'target' wajib diisi. Contoh: port_scan(target=\"192.168.1.1\")"
    auth_error = _require_target_authorization(args, "port_scan")
    if auth_error:
        return auth_error
    port_str = args.get("ports", "common")
    scan_type = args.get("scan_type", "quick")
    results = []

    common_ports = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445,
                    993, 995, 1433, 1521, 2049, 2082, 2083, 3306, 3389, 5432,
                    5900, 5984, 6379, 8080, 8443, 9000, 9090, 27017]

    if port_str == "common":
        ports = common_ports
    elif port_str == "1-1000":
        ports = list(range(1, 1001))
    elif port_str == "1-1024":
        ports = list(range(1, 1025))
    else:
        ports = []
        for part in port_str.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                ports.extend(range(int(a), int(b) + 1))
            else:
                ports.append(int(part))

    if scan_type == "quick":
        ports = [p for p in ports if p in common_ports] or ports[:50]

    with _Spinner(f"Scanning {target} ({len(ports)} ports)"):
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            r = sock.connect_ex((target, port))
            if r == 0:
                try:
                    service = socket.getservbyport(port)
                except Exception:  # noqa: BLE001
                    service = "unknown"
                results.append(f"  PORT {port:>5}/tcp  OPEN  {service}")
            sock.close()

    if not results:
        return f"[PORTS] No open ports found on {target} (scanned {len(ports)} ports)"
    return f"[PORTS] Open ports on {target} ({len(results)} open of {len(ports)} scanned):\n" + \
        "\n".join(results)


def _dns_lookup(domain, rtype):
    r = subprocess.run(
        ["dig", "+short", domain, rtype],
        capture_output=True, text=True, timeout=15, check=False
    )
    return r.stdout.strip()


def handle_dns_enum(args):
    domain = args.get("domain", "")
    if not domain:
        return "Error: Parameter 'domain' wajib diisi. Contoh: dns_enum(domain=\"example.com\")"
    action = args.get("action", "records")
    output = []

    if action in ("records", "all"):
        record_types = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]
        for rtype in record_types:
            result = _dns_lookup(domain, rtype)
            if result:
                output.append(f"  {rtype} Records:")
                for line in result.splitlines():
                    output.append(f"    {line}")
        if not output:
            output.append("  (no DNS records found)")

    if action in ("subdomains", "all"):
        common_subdomains = [
            "www", "mail", "ftp", "admin", "blog", "webmail", "pop3",
            "smtp", "api", "dev", "test", "staging", "vpn", "remote",
            "portal", "cpanel", "whm", "mysql", "backup", "proxy",
            "cdn", "static", "img", "docs", "wiki", "git", "jenkins",
            "jira", "confluence", "grafana", "prometheus", "monitor",
            "ns1", "ns2", "ns3", "mx", "chat", "help", "support",
            "status", "app", "beta", "demo", "shop", "store", "ssl",
            "cloud", "web", "server", "db", "redis", "mongo"
        ]
        output.append(f"\n  Subdomain brute-force ({len(common_subdomains)}):")
        found = 0
        for sd in common_subdomains:
            sd_target = f"{sd}.{domain}"
            try:
                result = _dns_lookup(sd_target, "A")
                if result:
                    ip = result.splitlines()[0].strip()
                    output.append(f"    {sd_target} -> {ip}")
                    found += 1
            except Exception:  # noqa: BLE001, S110
                pass
        output.append(f"  Found {found} subdomains")

    return f"[DNS] Enumeration for {domain}:\n" + "\n".join(output)


def handle_web_vuln_scan(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: web_vuln_scan(url=\"https://example.com\")"
    auth_error = _require_target_authorization(args, "web_vuln_scan")
    if auth_error:
        return auth_error
    checks = args.get("checks", "headers,info")
    _verify = _http_verify(args)
    output = []

    try:
        r = httpx.get(url, timeout=60, follow_redirects=True, verify=_verify)
    except Exception as e:  # noqa: BLE001
        return f"[WEB_VULN] Error accessing {url}: {e}"

    output.append(f"  URL: {url}")
    output.append(f"  Status: {r.status_code}")
    output.append(f"  Server: {r.headers.get('Server', 'N/A')}")
    output.append(f"  Content-Type: {r.headers.get('Content-Type', 'N/A')}")
    output.append(f"  Content-Length: {len(r.content)} bytes")

    if "headers" in checks or "all" in checks:
        output.append("\n  [Security Headers]")
        sec_headers = {
            "Strict-Transport-Security": "HSTS (HttpOnly)",
            "Content-Security-Policy": "CSP",
            "X-Frame-Options": "Clickjacking protection",
            "X-Content-Type-Options": "MIME-sniffing protection",
            "X-XSS-Protection": "XSS protection",
            "Referrer-Policy": "Referrer policy",
            "Permissions-Policy": "Permissions policy",
            "Set-Cookie": "Cookie flags (HttpOnly/Secure)",
        }
        for hdr, desc in sec_headers.items():
            val = r.headers.get(hdr, "MISSING")
            marker = "\033[31mMISSING\033[0m" if val == "MISSING" else "\033[32mPRESENT\033[0m"
            output.append(f"    {marker} {desc} ({hdr})")
            if val != "MISSING":
                output.append(f"      Value: {val[:100]}")

    if "info" in checks or "all" in checks:
        output.append("\n  [Server Information]")
        via = r.headers.get("Via", "")
        cf_ray = r.headers.get("CF-RAY", "")
        powered = r.headers.get("X-Powered-By", "")
        asp = r.headers.get("X-AspNet-Version", "")
        runtime = r.headers.get("X-Runtime", "")
        for hdr, label in [(via, "Via"), (cf_ray, "CF-RAY"),
                           (powered, "X-Powered-By"), (asp, "X-AspNet-Version"),
                           (runtime, "X-Runtime")]:
            if hdr:
                output.append(f"    {label}: {hdr}")

    if "sqli" in checks or "all" in checks:
        output.append("\n  [SQL Injection Test]")
        sqli_payloads = [
            ("'", "single quote"),
            ("' OR '1'='1", "OR true"),
            ("' UNION SELECT 1--", "UNION"),
            ("1' AND 1=1--", "AND true"),
            ("1' AND 1=2--", "AND false"),
        ]
        import urllib.parse
        for payload, desc in sqli_payloads:
            try:
                encoded = urllib.parse.quote(payload)
                test_url = f"{url}?id={encoded}"
                rr = httpx.get(test_url, timeout=10, verify=_verify)
                if rr.status_code == 200:
                    import html
                    body_lower = rr.text.lower()
                    sqli_indicators = [
                        "sql",
                        "mysql",
                        "syntax",
                        "uncaught",
                        "odbc",
                        "exception",
                        "warning",
                        "db_",
                        "column",
                        "rowCount",
                        "oracle",
                        "postgre"]
                    if any(ind in body_lower for ind in sqli_indicators):
                        output.append(
                            f"    \033[31mSUSPECT SQLi\033[0m (payload: {desc})")
                    else:
                        output.append(f"    OK (payload: {desc})")
                else:
                    output.append(f"    {rr.status_code} (payload: {desc})")
            except Exception:  # noqa: BLE001
                output.append(f"    Error (payload: {desc})")

    if "xss" in checks or "all" in checks:
        output.append("\n  [XSS Reflection Test]")
        xss_payloads = [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "\"><script>alert(1)</script>",
        ]
        import html
        import urllib.parse
        for payload in xss_payloads:
            try:
                encoded = urllib.parse.quote(payload)
                test_url = f"{url}?q={encoded}"
                rr = httpx.get(test_url, timeout=10, verify=_verify)
                if html.unescape(payload) in rr.text or payload in rr.text:
                    output.append(
                        "    \033[31mSUSPECT XSS\033[0m (payload reflected)")
                else:
                    output.append(
                        f"    No reflection (payload: {payload[:30]})")
            except Exception:  # noqa: BLE001
                output.append(f"    Error (payload: {payload[:30]})")

    return f"[WEB_VULN] Scan result for {url}:\n" + "\n".join(output)


def _whois_socket(domain):
    try:
        whois_server = "whois.iana.org"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect((whois_server, 43))
        sock.send(f"{domain}\r\n".encode())
        response = b""
        while True:
            data = sock.recv(4096)
            if not data:
                break
            response += data
        sock.close()
        text = response.decode(errors="replace")

        # Try to find authoritative whois server
        for line in text.splitlines():
            if "whois" in line.lower() and "." in line and "server" in line.lower():
                parts = line.split(":")
                if len(parts) > 1:
                    ref_server = parts[-1].strip()
                    if ref_server and ref_server != whois_server:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(15)
                        sock.connect((ref_server, 43))
                        sock.send(f"{domain}\r\n".encode())
                        response = b""
                        while True:
                            data = sock.recv(4096)
                            if not data:
                                break
                            response += data
                        sock.close()
                        return response.decode(errors="replace")
        return text
    except Exception:  # noqa: BLE001
        return ""


def handle_whois_lookup(args):
    target = args.get("target", "")
    if not target:
        return "Error: Parameter 'target' wajib diisi. Contoh: whois_lookup(target=\"example.com\")"
    with _Spinner(f"WHOIS lookup {target}"):
        try:
            r = subprocess.run(["whois", target], capture_output=True, text=True, timeout=30, check=False)
            output = r.stdout or r.stderr or _whois_socket(target)
        except FileNotFoundError:
            output = _whois_socket(target)
    if not output:
        return f"  No WHOIS data for {target}"
    lines = output.splitlines()
    important = []
    keywords = [
        "domain",
        "registrar",
        "registrant",
        "admin",
        "creation date",
        "expir",
        "name server",
        "status",
        "org",
        "organization",
        "email",
        "phone",
        "address",
        "country",
        "referral",
        "whois",
        "inetnum",
        "netname",
        "descr",
        "role",
        "nic-hdl",
        "mnt-by",
        "source"]
    for line in lines:
        if any(k.lower() in line.lower() for k in keywords):
            important.append(f"  {line.strip()}")
    if important:
        return f"[WHOIS] {target}:\n" + "\n".join(important[:40])
    return f"[WHOIS] {target}:\n" + "\n".join(f"  {l}" for l in lines[:30])


def handle_ssl_check(args):
    host = args.get("host", "")
    if not host:
        return "Error: Parameter 'host' wajib diisi. Contoh: ssl_check(host=\"example.com\")"
    port = int(args.get("port", 443))
    output = []

    try:
        ctx = ssl.create_default_context()  # type: ignore[attr-defined]
        with socket.create_connection((host, port), timeout=10) as sock, ctx.wrap_socket(sock, server_hostname=host) as ssock:  # type: ignore[union-attr]
                cert = ssock.getpeercert()
                output.append(f"  Host: {host}:{port}")
                output.append(f"  Protocol: {ssock.version()}")

                if cert:
                    subject_dict = {}
                    for part in cert.get('subject', []):
                        subject_dict.update(dict(part))
                    issuer_dict = {}
                    for part in cert.get('issuer', []):
                        issuer_dict.update(dict(part))
                    output.append(
                        f"  Subject: {subject_dict.get('commonName', 'N/A')}")
                    output.append(
                        f"  Issuer: {issuer_dict.get('organizationName', 'N/A')}")
                    output.append(
                        f"  Serial: {cert.get('serialNumber', 'N/A')}")
                    output.append(
                        f"  Valid From: {cert.get('notBefore', 'N/A')}")
                    output.append(
                        f"  Valid Until: {cert.get('notAfter', 'N/A')}")

                    import datetime
                    not_after = str(cert.get('notAfter', ''))
                    if not_after:
                        try:
                            exp = datetime.datetime.strptime(
                                not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
                            remaining = (exp - datetime.datetime.now(datetime.timezone.utc)).days
                            if remaining < 0:
                                output.append(
                                    f"  \033[31mEXPIRED ({abs(remaining)} days ago)\033[0m")
                            elif remaining < 30:
                                output.append(
                                    f"  \033[33mExpiring soon: {remaining} days\033[0m")
                            else:
                                output.append(
                                    f"  \033[32mValid: {remaining} days remaining\033[0m")
                        except Exception:  # noqa: BLE001
                            _console.print("[dim]Warning: Gagal parsing tanggal sertifikat[/dim]")

                    san = cert.get('subjectAltName', [])
                    if san:
                        domains = [str(v) for k, v in san if k == 'DNS']
                        output.append(
                            f"  SAN: {', '.join(domains[:5])}{'...' if len(domains) > 5 else ''}")
                else:
                    output.append("  No certificate returned")
    except ssl.SSLError as e:
        output.append(f"  SSL Error: {e}")
    except Exception as e:  # noqa: BLE001
        output.append(f"  Connection Error: {e}")

    if not output:
        return f"[SSL] No response from {host}:{port}"
    return f"[SSL] Certificate check for {host}:{port}\n" + "\n".join(output)


def handle_dir_bruteforce(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: dir_bruteforce(url=\"https://example.com\")"
    auth_error = _require_target_authorization(args, "dir_bruteforce")
    if auth_error:
        return auth_error
    wordlist_size = args.get("wordlist", "small")
    extensions = args.get("extensions", "")
    ext_list = [f".{e.strip()}" for e in extensions.split(",")
                if e.strip()] if extensions else []
    _verify = _http_verify(args)

    # Wordlist kumulatif eksplisit: medium = small + tambahan medium,
    # large = small + medium + tambahan large. (Dulu large dibangun dari
    # list medium yang masih kosong, sehingga large tak pernah berisi
    # entri small/medium — fitur large praktis tidak jalan.)
    _small = ["admin", "login", "wp-admin", "backup", "config", "db", "sql",
              "admin.php", "login.php", "config.php", ".env", "wp-config.php",
              "robots.txt", "sitemap.xml", "index.php", "index.html", "test",
              "api", "v1", "v2", "static", "assets", "uploads", "images",
              "css", "js", "private", "secret", "hidden", "tmp", "temp",
              "logs", "error_log", "phpinfo.php", "info.php", "shell.php",
              "cmd.php", "upload.php", "download.php", "cgi-bin", "cron",
              "setup", "install", "readme.html", "license.txt"]
    _medium_extra = [
        "app", "src", "lib", "include", "inc", "modules", "plugins",
        "themes", "templates", "cache", "data", "dump", "export",
        "import", "manager", "panel", "dashboard", "user", "users",
        "member", "members", "account", "register", "signup", "forgot",
        "reset", "password", "profile", "edit", "settings", "preferences",
        "ajax", "rest", "graphql", "ws", "websocket", "soap", "xmlrpc",
        "rss", "feed", "atom", "json", "csv", "txt", "xml", "pdf",
        "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt",
    ]
    _large_extra = [
        "0", "1", "2", "3", "a", "b", "c", "d", "e", "f", "g", "h",
        "old", "new", "bak", "copy", "original", "latest", "final",
        "working", "dev", "development", "staging", "prod", "production",
        "local", "live", "master", "main", "release", "patch", "hotfix",
        "docker", "docker-compose.yml", "Dockerfile", "Makefile",
        "package.json", "composer.json", "pom.xml", "build.gradle",
        "Procfile", "requirements.txt", "Gemfile", "Podfile",
        ".gitignore", ".htaccess", ".htpasswd", ".svn", ".DS_Store",
        "Thumbs.db", "crossdomain.xml", "clientaccesspolicy.xml",
        "web.config", "application.properties", "log4j.properties",
        "struts.xml", "web.xml", "index.jsp", "default.aspx",
    ]

    if wordlist_size == "large":
        raw_paths = _small + _medium_extra + _large_extra
    elif wordlist_size == "medium":
        raw_paths = _small + _medium_extra
    else:
        # Nilai tak dikenal (termasuk default "small") -> small yang aman.
        raw_paths = _small
    # Dedup jaga urutan (dulu "pdf" ganda di medium).
    paths = list(dict.fromkeys(raw_paths))

    found = []
    with _Spinner(f"Bruteforcing {url} ({len(paths)} paths)"):
        for path in paths:
            test_url = f"{url}/{path}"
            try:
                rr = httpx.get(test_url, timeout=5, verify=_verify)
                if rr.status_code in (
                        200, 201, 204, 301, 302, 307, 308, 401, 403):
                    size = len(rr.content)
                    found.append(
                        f"  {rr.status_code:>3}  {size:>8}b  {test_url}")
            except Exception:  # noqa: BLE001
                _console.print(f"[dim]Warning: Gagal mengakses {test_url}[/dim]")

            if ext_list:
                for ext in ext_list:
                    test_url_ext = f"{url}/{path}{ext}"
                    try:
                        rr = httpx.get(test_url_ext, timeout=5, verify=_verify)
                        if rr.status_code in (
                                200, 201, 204, 301, 302, 307, 308, 401, 403):
                            size = len(rr.content)
                            found.append(
                                f"  {rr.status_code:>3}  {size:>8}b  {test_url_ext}")
                    except Exception:  # noqa: BLE001
                        _console.print(f"[dim]Warning: Gagal mengakses {test_url_ext}[/dim]")

        if not found:
            return f"[DIRBRUTE] No paths found on {url} ({len(paths)} tested)"
    return f"[DIRBRUTE] Found {len(found)} paths on {url}:\n" + \
        "\n".join(found)


def handle_cve_search(args):
    query = args.get("query", "")
    if not query:
        return "Error: Parameter 'query' wajib diisi. Contoh: cve_search(query=\"apache 2.4.49\")"
    _verify = _http_verify(args)
    with _Spinner(f"Searching CVEs for {query}"):
        try:
            search_url = f"https://cve.circl.lu/api/search/{query.replace(' ', '/')}"
            r = httpx.get(
                search_url,
                timeout=20,
                follow_redirects=True,
                verify=_verify)
            if r.status_code == 200:
                data = r.json()
            else:
                data = None
        except Exception:  # noqa: BLE001
            data = None

    output = []
    if data and isinstance(data, list):
        cves = data[:15]
        for cve in cves:
            cve_id = cve.get("id", "N/A")
            summary = cve.get("summary", "")[:200]
            cvss = cve.get("cvss_score", "N/A")
            severity = cve.get("severity", "")
            output.append(f"  {cve_id} (CVSS: {cvss} {severity})")
            output.append(f"    {summary}")
            output.append("")
        if not output:
            output.append(f"  No CVEs found for '{query}'")
    else:
        output.append("  CIRCL API unavailable, searching via web...")
        try:
            results = DDGS().text(f"CVE {query}", max_results=5)
            if results:
                for r in results:
                    output.append(f"  {r['title']}")
                    output.append(f"    {r['href']}")
                    output.append(f"    {r['body'][:200]}")
                    output.append("")
            else:
                output.append(f"  No results found for '{query}'")
        except Exception:  # noqa: BLE001
            output.append(f"  Error searching for '{query}'")

    return f"[CVE] Results for '{query}':\n" + "\n".join(output)


def handle_tech_detect(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: tech_detect(url=\"https://example.com\")"
    deep = args.get("deep", "simple")
    timeout = args.get("timeout", 60)
    _verify = _http_verify(args)
    output = []
    tech = {}

    try:
        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            verify=_verify,
            headers={
                "User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        return f"[TECH] Error accessing {url}: {e}"

    output.append(f"  URL: {url}")
    output.append(f"  Status: {r.status_code}")
    output.append(f"  Content-Type: {r.headers.get('Content-Type', 'N/A')}")

    output.append("\n  [HTTP Headers]")
    interesting_headers = [
        "Server", "X-Powered-By", "X-Generator", "X-Drupal-Cache",
        "X-Drupal-Dynamic-Cache", "X-Varnish", "X-Cache", "X-Cache-Hits",
        "CF-RAY", "X-Server-Powered-By", "X-AspNet-Version", "X-Runtime",
        "Via", "X-Proxy-Cache", "X-Served-By", "X-CMS", "X-Version",
        "Access-Control-Allow-Origin", "X-Frame-Options",
        "X-Content-Type-Options", "Strict-Transport-Security"
    ]
    for h in interesting_headers:
        val = r.headers.get(h)
        if val:
            output.append(f"    {h}: {val}")

    output.append("\n  [Cookies]")
    for cookie in r.cookies:
        name = getattr(cookie, "name", str(cookie))
        output.append(f"    {name}")

    if deep == "deep":
        html = r.text.lower()

        detectors = {
            "WordPress": ["wp-content", "wp-includes", "wp-json", "wordpress"],
            "Drupal": ["drupal", "drupal.js", "sites/default"],
            "Joomla": ["joomla", "com_content", "com_users"],
            "Laravel": ["laravel", "csrf-token", "livewire"],
            "Django": ["csrfmiddlewaretoken", "django", "__admin"],
            "Ruby on Rails": ["rails", "csrf-param", "authenticity_token"],
            "React": ["react", "react-dom", "__NEXT_DATA__", "nextjs", "next/js"],
            "Vue.js": ["vue", "vuejs", "v-bind", "v-model", "vue-router"],
            "Angular": ["ng-app", "ng-controller", "angular", "ng-version"],
            "jQuery": ["jquery", "$.fn", "jquery-"],
            "Bootstrap": ["bootstrap", "bootstrap-", "bs-"],
            "Tailwind": ["tailwind", "tailwindcss"],
            "Alpine.js": ["alpinejs", "x-data", "x-init", "x-on"],
            "HTMX": ["htmx", "hx-get", "hx-post", "hx-trigger"],
            "PHP": [".php", "php-session"],
            "ASP.NET": ["__viewstate", "__eventvalidation", "asp.net", "aspnet"],
            "Java": ["javax.faces", "jsf", "spring", "struts"],
            "Nginx": ["nginx", "nginx/"],
            "Apache": ["apache/", "apache", ".htaccess"],
            "Cloudflare": ["cloudflare", "cf-ray", "__cfduid"],
            "Google Analytics": ["gtag", "ga.js", "analytics.js", "google-analytics"],
            "Facebook Pixel": ["fbq(", "facebook pixel", "connect.facebook"],
            "Hotjar": ["hotjar", "hj("],
            "Intercom": ["intercom", "intercom-script"],
            "Stripe": ["stripe", "pk_live", "sk_live"],
            "Google Maps": ["maps.google", "google.maps", "maps.googleapis"],
            "reCAPTCHA": ["recaptcha", "g-recaptcha"],
            "Disqus": ["disqus", "disqus_thread"],
            "Algolia": ["algolia", "algoliasearch"],
            "Sentry": ["sentry", "raven-"],
            "New Relic": ["newrelic", "nr-"],
        }

        output.append("\n  [Detected Technologies]")
        for name, sigs in sorted(detectors.items()):
            for sig in sigs:
                if sig in html or sig in r.text.lower():
                    tech[name] = tech.get(name, 0) + 1
                    break
        if tech:
            for name in sorted(tech, key=lambda k: -tech[k]):
                output.append(f"    {name}")
        else:
            output.append("    (no specific tech detected)")

        output.append("\n  [HTML Analysis]")
        title_match = re.search(
            r'<title[^>]*>(.*?)</title>',
            r.text,
            re.IGNORECASE | re.DOTALL)
        if title_match:
            output.append(f"    Title: {title_match.group(1).strip()[:100]}")
        desc_match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            r.text,
            re.IGNORECASE)
        if desc_match:
            output.append(f"    Meta Desc: {desc_match.group(1)[:120]}")
        script_count = len(
            re.findall(
                r'<script[^>]*src=["\']([^"\']+)["\']',
                r.text,
                re.IGNORECASE))
        css_count = len(
            re.findall(
                r'<link[^>]+href=["\']([^"\']+\.css)["\']',
                r.text,
                re.IGNORECASE))
        output.append(f"    External JS: {script_count}")
        output.append(f"    External CSS: {css_count}")

    return f"[TECH] Tech Stack for {url}:\n" + "\n".join(output)


def handle_api_discover(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: api_discover(url=\"https://example.com\")"
    depth = int(args.get("depth", 2))
    _verify = _http_verify(args)
    output = []

    try:
        r = httpx.get(
            url,
            timeout=60,
            follow_redirects=True,
            verify=_verify,
            headers={
                "User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        return f"[API] Error accessing {url}: {e}"

    text = r.text
    apis = set()

    output.append(f"  Target: {url}")

    output.append("\n  [Form Actions]")
    form_actions = re.findall(
        r'<form[^>]+action=["\']([^"\']+)["\']',
        text,
        re.IGNORECASE)
    for fa in form_actions:
        apis.add(fa)
        output.append(f"    {fa}")
    if not form_actions:
        output.append("    (no forms found)")

    output.append("\n  [Inline API Calls]")
    fetch_patterns = [
        r'fetch\(["\']([^"\']+)["\']',
        r'axios\.\w+\(["\']([^"\']+)["\']',
        r'\$\.(?:get|post|ajax)\(["\']([^"\']+)["\']',
        r'\.ajax\(\{.*?url:\s*["\']([^"\']+)["\']',
        r'XMLHttpRequest[^;]*?\.open\(["\'][A-Z]+["\'],\s*["\']([^"\']+)["\']',
        r'url:\s*["\']([^"\']+)["\']',
    ]
    for pat in fetch_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            apis.add(m.group(1))
            output.append(f"    {m.group(1)[:100]}")

    if depth >= 2:
        output.append("\n  [Script File URLs]")
        js_srcs = re.findall(
            r'<script[^>]+src=["\']([^"\']+)["\']',
            text,
            re.IGNORECASE)
        for js_src in js_srcs[:10]:
            js_url = js_src if js_src.startswith("http") else (
                url.rstrip("/") + "/" + js_src.lstrip("/"))
            try:
                rr = httpx.get(
                    js_url, timeout=10, verify=_verify, headers={
                        "User-Agent": "Mozilla/5.0"})
                if rr.status_code == 200:
                    inner_patterns = [
                        r'["\'](https?://[^"\']*api[^"\']*)["\']',
                        r'["\'](/api/[^"\']+)["\']',
                        r'["\'](/v[0-9]+/[^"\']+)["\']',
                        r'["\'](/graphql)[^"\']*["\']',
                        r'["\'](/rest/[^"\']+)["\']',
                        r'["\'](/[^"\']*(?:endpoint|webhook|callback)[^"\']*)["\']',
                    ]
                    for ipat in inner_patterns:
                        for m in re.finditer(ipat, rr.text, re.IGNORECASE):
                            apis.add(m.group(1))
            except Exception:  # noqa: BLE001
                _console.print(f"[dim]Warning: Gagal fetch JS: {js_url}[/dim]")

        if apis:
            output.append("\n  [Unique API Paths Found]")
            for api in sorted(apis)[:40]:
                output.append(f"    {api}")
        else:
            output.append("\n  [Unique API Paths Found]")
            output.append("    (none found)")

        output.append("\n  [API Patterns]")
        api_patterns_found = set()
        for api in apis:
            parts = api.rstrip("/").split("/")
            for i, p in enumerate(parts):
                if p in (
                    "api",
                    "v1",
                    "v2",
                    "v3",
                    "rest",
                    "graphql",
                    "webhook",
                        "endpoint"):
                    pattern = "/".join(parts[:i + 2])
                    api_patterns_found.add(pattern)
        if api_patterns_found:
            for p in sorted(api_patterns_found)[:15]:
                output.append(f"    /{p.lstrip('/')}")
        else:
            output.append("    (no specific API pattern)")

    return f"[API] API Discovery for {url}:\n" + "\n".join(output)


def handle_source_map_check(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: source_map_check(url=\"https://example.com\")"
    _verify = _http_verify(args)
    output = []

    try:
        r = httpx.get(
            url,
            timeout=60,
            follow_redirects=True,
            verify=_verify,
            headers={
                "User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        return f"[SOURCEMAP] Error accessing {url}: {e}"

    output.append(f"  Target: {url}")

    output.append("\n  [Source Map Discovery]")
    js_srcs = re.findall(
        r'<script[^>]+src=["\']([^"\']+\.js[^"\']*)["\']',
        r.text,
        re.IGNORECASE)
    found_maps = []

    for js_src in js_srcs[:20]:
        js_url = js_src if js_src.startswith("http") else (
            url.rstrip("/") + "/" + js_src.lstrip("/"))
        if js_url.endswith(".map"):
            found_maps.append(js_url)
            continue
        map_url = js_url + ".map"
        alt_map = re.sub(r'\.js$', '.map', js_url)
        for mu in [map_url, alt_map]:
            try:
                mr = httpx.head(mu, timeout=5, verify=_verify)
                if mr.status_code in (200, 204):
                    found_maps.append(mu)
            except Exception:  # noqa: BLE001
                _console.print(f"[dim]Warning: Gagal cek source map: {mu}[/dim]")

    comment_maps = re.findall(r'//#\s*sourceMappingURL=(.+\.map)', r.text)
    if comment_maps:
        for cm in comment_maps:
            if not cm.startswith("http"):
                cm = url.rstrip("/") + "/" + cm.lstrip("/")
            found_maps.append(cm)

    if found_maps:
        output.append("  \033[31mEXPOSED SOURCE MAPS DETECTED!\033[0m")
        for fm in sorted(set(found_maps)):
            output.append(f"    {fm}")
    else:
        output.append("  No source maps found (good)")

    if found_maps:
        output.append("\n  [Content from First Source Map]")
        try:
            sm_url = next(iter(set(found_maps)))
            sm_r = httpx.get(sm_url, timeout=10, verify=_verify)
            if sm_r.status_code == 200:
                sm_data = sm_r.json()
                sources = sm_data.get("sources", [])
                names = sm_data.get("names", [])
                if sources:
                    output.append(f"    Original sources ({len(sources)}):")
                    for s in sources[:15]:
                        output.append(f"      {s}")
                if names:
                    output.append(f"    Identifiers ({len(names)}):")
                    for n in names[:20]:
                        output.append(f"      {n}")
        except Exception:  # noqa: BLE001
            output.append("    (could not parse source map)")

    return f"[SOURCEMAP] Source Map Check for {url}:\n" + "\n".join(output)


def handle_form_analyze(args):
    url = args.get("url", "").rstrip("/")
    if not url:
        return "Error: Parameter 'url' wajib diisi. Contoh: form_analyze(url=\"https://example.com/login\")"
    _verify = _http_verify(args)
    output = []

    try:
        r = httpx.get(
            url,
            timeout=60,
            follow_redirects=True,
            verify=_verify,
            headers={
                "User-Agent": "Mozilla/5.0"})
    except Exception as e:  # noqa: BLE001
        return f"[FORM] Error accessing {url}: {e}"

    output.append(f"  Target: {url}")
    output.append(f"  Status: {r.status_code}")

    forms = re.findall(
        r'(<form[^>]*>(.*?)</form>)',
        r.text,
        re.IGNORECASE | re.DOTALL)

    if not forms:
        output.append("\n  No forms found")
        return f"[FORM] Form Analysis for {url}:\n" + "\n".join(output)

    output.append(f"\n  Forms found: {len(forms)}")

    for i, (form_html, form_body) in enumerate(forms):
        output.append(f"\n  {'='*40}")
        output.append(f"  Form #{i+1}")

        action = re.search(
            r'action=["\']([^"\']*)["\']',
            form_html,
            re.IGNORECASE)
        method = re.search(
            r'method=["\']([^"\']*)["\']',
            form_html,
            re.IGNORECASE)
        enctype = re.search(
            r'enctype=["\']([^"\']*)["\']',
            form_html,
            re.IGNORECASE)

        output.append(f"    Action: {action.group(1) if action else '(self)'}")
        output.append(
            f"    Method: {method.group(1).upper() if method else 'GET'}")
        if enctype:
            output.append(f"    Enctype: {enctype.group(1)}")

        output.append("\n    [Fields]")
        inputs = re.findall(r'(<input[^>]*>)', form_body, re.IGNORECASE)
        selects = re.findall(
            r'(<select[^>]*>.*?</select>)',
            form_body,
            re.IGNORECASE | re.DOTALL)
        textareas = re.findall(
            r'(<textarea[^>]*>.*?</textarea>)',
            form_body,
            re.IGNORECASE | re.DOTALL)

        for inp in inputs:
            inp_type = re.search(
                r'type=["\']([^"\']*)["\']', inp, re.IGNORECASE)
            inp_name = re.search(
                r'name=["\']([^"\']*)["\']', inp, re.IGNORECASE)
            inp_val = re.search(
                r'value=["\']([^"\']*)["\']', inp, re.IGNORECASE)
            inp_auto = re.search(
                r'autocomplete=["\']([^"\']*)["\']',
                inp,
                re.IGNORECASE)

            t = inp_type.group(1).lower() if inp_type else "text"
            n = inp_name.group(1) if inp_name else "(unnamed)"
            v = inp_val.group(1) if inp_val else "(empty)"

            tag = ""
            if t == "hidden":
                tag = " \033[33m[HIDDEN]\033[0m"
            if inp_auto and inp_auto.group(1).lower() == "off":
                tag += " \033[31m[autocomplete=off]\033[0m"

            output.append(f"      [{t}] {n} = {v[:40]}{tag}")

        for sel in selects:
            sel_name = re.search(
                r'name=["\']([^"\']*)["\']', sel, re.IGNORECASE)
            n = sel_name.group(1) if sel_name else "(unnamed)"
            options = re.findall(
                r'<option[^>]*value=["\']([^"\']*)["\']',
                sel,
                re.IGNORECASE)
            output.append(f"      [select] {n} (options: {options[:5]})")

        for ta in textareas:
            ta_name = re.search(r'name=["\']([^"\']*)["\']', ta, re.IGNORECASE)
            n = ta_name.group(1) if ta_name else "(unnamed)"
            output.append(f"      [textarea] {n}")

        csrf_inputs = re.findall(
            r'<input[^>]*name=["\']([^"\']*(?:csrf|token|authenticity|_token)[^"\']*)["\'][^>]*>',
            form_html,
            re.IGNORECASE)
        if csrf_inputs:
            output.append("    \033[32m[CSRF Protection Detected]\033[0m")
            for c in csrf_inputs:
                output.append(f"      CSRF field: {c}")

    return f"[FORM] Form Analysis for {url}:\n" + "\n".join(output)
